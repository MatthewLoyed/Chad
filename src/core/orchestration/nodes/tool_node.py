import logging
import json
from typing import Dict, Any, Callable, Optional
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from src.core.orchestration.nodes.plan_parser import _resolve_step, _next_actionable_step
from src.core.orchestration.plan_service import PlanService
from src.core.tools import failure, limits

logger = logging.getLogger("ChadSupervisor")

# Tool outputs (web fetches, file reads, large API responses) are unbounded and
# accumulate in conversation history forever — nothing else in the orchestration
# pipeline truncates them (#38). Cap each at a generous-but-bounded size so a single
# oversized result can't dominate the context window across the rest of the session.
# Configurable via tools.tool_output_max_chars — see src/core/tools/limits.py.


def _max_tool_output_chars(tool_name: Optional[str] = None) -> int:
    if tool_name in ("browser_snapshot", "browser_scroll"):
        return int(limits.get("browser_snapshot_max_chars"))
    return int(limits.get("tool_output_max_chars"))


def _truncate_large_tool_outputs(messages: list) -> None:
    """Truncate oversized ``ToolMessage`` content in place.

    Runs after schema-injection/plan-mutation parsing (which needs the untruncated
    JSON), so it only affects what gets persisted into history going forward.

    FAILURES ARE TRUNCATED DIFFERENTLY, on purpose. Two reasons:

    1. Budget. On local inference attempts are free but context is not, so N failed
       attempts must not cost N × 8000 chars. A failure the model can act on needs
       its class, its cause, and its next actions — not its stack trace. Failed
       output therefore gets a much tighter cap.
    2. Direction. Head-only truncation is actively WRONG for a failure: NEXT_ACTIONS
       is rendered at the end, so cutting the tail removes the only part the model
       can act on and keeps the noise. `clip_failure_text` preserves the tail.

    BROWSER SNAPSHOTS PRESERVE SET-OF-MARK:
    Browser snapshots place the actionable Set-of-Mark menu at the top. If context
    budget forces truncation, the Set-of-Mark menu is kept intact so the model never
    loses the interactive handles needed to click or type.
    """
    for msg in messages:
        if not isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
            continue

        if failure.failure_signature(msg.content):
            msg.content = failure.clip_failure_text(msg.content)
            continue

        tool_name = getattr(msg, "name", None)
        cap = _max_tool_output_chars(tool_name)
        if len(msg.content) <= cap:
            continue

        # Browser tools return JSON envelopes, often prefixed by the sanitizer.
        # Cutting serialized JSON makes it unparsable and hides success/scroll
        # metadata from the browser controller. Clip data strings, then serialize.
        if tool_name and tool_name.startswith('browser_'):
            prefix = '[TOOL_DATA]\n' if msg.content.startswith('[TOOL_DATA]\n') else ''
            try:
                envelope = json.loads(msg.content[len(prefix):])
            except ValueError:
                envelope = None
            if isinstance(envelope, dict) and isinstance(envelope.get('result'), (list, str)):
                original_parts = envelope['result']
                marker = '\n[...browser text clipped; inspect a specific element...]\n'
                def clipped(value, size):
                    if isinstance(value, str) and len(value) > size:
                        half = max(0, (size - len(marker)) // 2)
                        return value[:half] + marker + value[-half:] if half else marker
                    if isinstance(value, list):
                        return [clipped(part, size) for part in value]
                    if isinstance(value, dict):
                        return {key: clipped(part, size) for key, part in value.items()}
                    return value
                parts_count = len(original_parts) if isinstance(original_parts, list) else 1
                size = max(100, cap // max(1, parts_count) - 400)
                while True:
                    envelope['result'] = clipped(original_parts, size)
                    serialized = prefix + json.dumps(envelope, ensure_ascii=False)
                    if len(serialized) <= cap or size <= 100:
                        break
                    size = max(100, size // 2)
                msg.content = serialized
                continue

        omitted = len(msg.content) - cap

        # Semantic preservation for browser snapshots with Set-of-Mark menus:
        # Never sever the Set-of-Mark handles; keep the entire SoM block intact and
        # truncate only the following raw snapshot body.
        som_marker = "### Interactive Elements (Set-of-Mark)"
        if som_marker in msg.content and msg.content.startswith(som_marker):
            body_marker = "### Pruned Snapshot"
            if body_marker in msg.content:
                som_part, body_part = msg.content.split(body_marker, 1)
                avail_for_body = max(1000, cap - len(som_part) - len(body_marker))
                omitted_body = max(0, len(body_part) - avail_for_body)
                truncated_body = body_part[:avail_for_body]
                msg.content = (
                    som_part
                    + body_marker
                    + truncated_body
                    + f"\n\n[...truncated, {omitted_body} more snapshot characters omitted to keep context bounded...]"
                )
                continue

        msg.content = (
            msg.content[:cap]
            + f"\n\n[...truncated, {omitted} more characters omitted to keep context bounded...]"
        )


def make_tool_node(base_tool_node: Any, tool_registry: Optional[Any] = None) -> Callable:
    """Factory creating the wrapped tool node using the base ToolNode."""
    
    async def wrapped_tool_node(state: Dict[str, Any], config: RunnableConfig):
        """Executes tools and extracts any newly discovered schemas for injection."""
        import time as _time
        _t0 = _time.perf_counter()
        last_msg = state.get("messages", [])[-1] if state.get("messages") else None
        if last_msg and getattr(last_msg, "tool_calls", None):
            for tc in last_msg.tool_calls:
                args_str = str(tc.get("args") or {})
                if len(args_str) > 100:
                    args_str = args_str[:97] + "..."
                logger.info(f"[ TOOL RUN ] -> {tc.get('name')}({args_str})")

        # 1. Run the standard ToolNode
        result = await base_tool_node.ainvoke(state, config)
        # A prior checkpoint's pause is not a new approval for this call.
        result["pending_approval"] = None
        _duration_ms = round((_time.perf_counter() - _t0) * 1000.0, 1)

        # 2. Check for newly discovered tools to inject into state
        # We look for '_injected_schemas' in the tool outputs
        new_tools = list(state.get("tools") or [])
        found_new = False
        
        for msg in result.get("messages", []):
            if isinstance(msg, ToolMessage):
                content_preview = str(msg.content or "")
                if len(content_preview) > 120:
                    content_preview = content_preview[:117] + "..."
                logger.info(f"[ TOOL RESULT ] <- {getattr(msg, 'name', 'tool')} ({_duration_ms}ms): {content_preview}")
                try:
                    # Tool outputs are strings; try to parse as JSON to find injection metadata
                    content = json.loads(msg.content)
                    if isinstance(content, dict):
                        # 1. Handle Tool Schema Injection (Breadth)
                        # Check both root and nested result for injection metadata
                        injected = content.get("_injected_schemas")
                        if not injected and isinstance(content.get("result"), dict):
                            injected = content["result"].get("_injected_schemas")
                            
                        if injected:
                            for schema in injected:
                                name = schema.get("name")
                                if name and not any(t.get("name") == name for t in new_tools):
                                    logger.info(f"[ INJECTION ] Injecting newly discovered tool: {name}")
                                    new_tools.append(schema)
                                    found_new = True
                        
                        # 2. Handle Skill Activation (Depth)
                        # The load_skill tool returns _loaded_skill signal at root
                        if "_loaded_skill" in content:
                            skill_name = content["_loaded_skill"]
                            # The full content is in 'result' or msg.content
                            skill_content = content.get("result") or msg.content
                            
                            logger.info(f"[ SKILL ] Activating skill in state: {skill_name}")
                            result["loaded_skill"] = skill_content
                            result["loaded_skill_name"] = skill_name

                            # Dynamically inject schemas for all tools bundled with this skill
                            if tool_registry and hasattr(tool_registry, "get_schemas_for_skill"):
                                skill_schemas = tool_registry.get_schemas_for_skill(skill_name)
                                for schema in skill_schemas:
                                    s_name = schema.get("name")
                                    if s_name and not any(t.get("name") == s_name for t in new_tools):
                                        logger.info(f"[ INJECTION ] Injecting skill-bundled tool: {s_name} for skill {skill_name}")
                                        new_tools.append(schema)
                                        found_new = True
                            
                except (json.JSONDecodeError, TypeError):
                    # Not a JSON response or doesn't have the injection key, skip
                    pass
        
        if found_new:
            # Update the 'tools' field in state for the next turn
            result["tools"] = new_tools

        # 3. Handle plan mutations and voice/control signals from tool outputs
        for msg in result.get("messages", []):
            if not isinstance(msg, ToolMessage):
                continue
            try:
                content = json.loads(msg.content)
                if not isinstance(content, dict):
                    continue

                if content.get("_pause_for_input"):
                    logger.info("[ TOOL ] pause_for_input called. Setting pause flag in state.")
                    result["paused_for_input"] = True

                if content.get("_terminal_budget") or "Browser kill-switch" in str(msg.content):
                    logger.warning("[ TOOL ] Action budget exhausted. Marking budget_exceeded in state.")
                    result["budget_exceeded"] = True
                    if getattr(msg, "name", "").startswith("browser_") or "Browser kill-switch" in str(msg.content):
                        result["browser_budget_exhausted"] = True

                if content.get("_pending_approval"):
                    from src.core.orchestration.context import pending_approval_var

                    from src.core.orchestration import approval_store
                    entry = approval_store.peek(content.get("approval_token"))
                    pending = (entry.get("payload") if entry else None) or pending_approval_var.get() or {
                        "token": content.get("approval_token"),
                        "tool_name": getattr(msg, "name", None),
                        "decision_type": content.get("decision_type"),
                        "prompt": content.get("result"),
                        "options": content.get("options"),
                        "title": content.get("title"),
                        "questions": content.get("questions"),
                    }
                    logger.info(
                        f"[ TOOL ] {pending.get('tool_name')} awaiting user input "
                        f"(token={pending.get('token')}). Pausing graph."
                    )
                    # Persist the exact atomic call, including specialist calls. Approval
                    # must replay this call without asking a model to reconstruct arguments.
                    pending = dict(pending)
                    if content.get("tool_call"):
                        pending["tool_call"] = content["tool_call"]
                        pending["tool_name"] = content.get("tool_name") or pending.get("tool_name")
                    if not pending.get("tool_call"):
                        for source in reversed(state.get("messages") or []):
                            match = next((c for c in getattr(source, "tool_calls", [])
                                          if c.get("id") == msg.tool_call_id
                                          and c.get("name") == pending.get("tool_name")), None)
                            if match:
                                pending["tool_call"] = json.loads(json.dumps(match))
                                break
                    result["pending_approval"] = pending
                    result["final_response"] = (
                        pending.get("title") or pending.get("prompt") or ""
                    )

                if content.get("_voice_escalate"):
                    logger.info("[ TOOL ] escalate_to_agent called. Flagging voice handoff.")
                    result["voice_escalate"] = True
                    result["voice_escalate_reason"] = content.get("reason", "")

                plan = list(state.get("active_plan") or [])
                if not plan:
                    continue

                active_id = state.get("plan_step", "")

                if content.get("_step_complete"):
                    # Resolve resiliently — a wrong/hallucinated step_id must not strand the plan.
                    step = _resolve_step(plan, content.get("step_id"), active_id)
                    if step:
                        step["status"] = "completed"
                        if content.get("summary"):
                            step["summary"] = content.get("summary")
                        logger.info(
                            f"[ TOOL ] Step '{step.get('id')}' marked COMPLETED "
                            f"(requested id='{content.get('step_id')}')."
                        )
                    else:
                        logger.warning(f"[ TOOL ] complete_step: no resolvable step for id='{content.get('step_id')}'.")
                    next_step = _next_actionable_step(plan)
                    result["active_plan"] = plan
                    result["plan_step"] = next_step["id"] if next_step else ""
                    result["failure_context"] = None
                    PlanService.emit_snapshot(state, plan)

                elif content.get("_step_failed"):
                    reason = content.get("reason", "Unknown reason")
                    step = _resolve_step(plan, content.get("step_id"), active_id)
                    failed_id = step.get("id") if step else content.get("step_id")
                    if step:
                        # Keep the step actionable — supervisor retries with a different
                        # approach. Marking terminal "failed" + scout/replan was wiping
                        # good mid-plan progress (HITL 2026-07-24).
                        step["status"] = "pending"
                        step["_turn_count"] = 0
                        logger.warning(
                            f"[ TOOL ] Step '{failed_id}' reported failed via fail_step — "
                            f"re-opened for a different approach."
                        )
                    result["active_plan"] = plan
                    result["plan_step"] = failed_id or active_id or ""
                    result["failure_context"] = (
                        f"Step '{failed_id}' could not be completed with the previous approach: "
                        f"{reason}\n"
                        f"Stay on this step. Do NOT create_plan or re-scout. Try a different "
                        f"tool/delegate/path, then complete_step when the deliverable exists."
                    )
                    PlanService.emit_snapshot(state, plan)

            except (json.JSONDecodeError, TypeError):
                pass

        # Voice follow-up round counter (hubspace summary pass)
        if state.get("category") == "voice" and result.get("messages"):
            result["voice_tool_rounds"] = state.get("voice_tool_rounds", 0) + 1

        # Chat lane tool counter (1-hop read tool pass)
        if (state.get("turn_mode") == "chat" or state.get("category") == "chat") and result.get("messages"):
            result["chat_tool_rounds"] = state.get("chat_tool_rounds", 0) + 1

        _truncate_large_tool_outputs(result.get("messages") or [])

        return result
        
    return wrapped_tool_node
