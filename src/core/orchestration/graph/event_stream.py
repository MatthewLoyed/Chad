"""Native LangGraph event-stream consumption for a single turn.

Extracted from LangGraphEngine.invoke() (#302). Runs `graph.astream_events`,
forwards streaming tokens/thinking/usage to the turn's callbacks, detects
early termination for tool-approval gates and node timeouts, and derives the
final response text once the graph settles.
"""
import re
import json
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage
from langgraph.types import Command
from uuid import uuid4
from langgraph.errors import NodeTimeoutError

logger = logging.getLogger("LangGraphEngine")


async def _safe_call(cb, *args, **kwargs):
    try:
        if asyncio.iscoroutinefunction(cb):
            await cb(*args, **kwargs)
        else:
            cb(*args, **kwargs)
    except Exception as ex:
        logger.warning(f"[ GRAPH ] Streaming callback error: {ex}")


async def run_turn_stream(
    graph: Any,
    config: Dict[str, Any],
    initial_state: Dict[str, Any],
    effective_thread_id: Optional[str],
    on_token: Optional[Callable] = None,
    on_thinking: Optional[Callable] = None,
    on_usage: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Run the graph via astream_events and return the resolved final state."""
    # --- TOOL PERSISTENCE MERGE ---
    # If we have a thread_id and tools in the initial_state, we must merge them
    # with any tools already in the persisted state to prevent overwriting
    # tools that were injected via 'search_for_tool' in previous turns.
    if effective_thread_id and "tools" in initial_state:
        try:
            # Timeout guard: aget_state can hang on Windows with psycopg async pools.
            # If it takes >5s, skip the merge and proceed with fresh tools only.
            existing_state = await asyncio.wait_for(
                graph.aget_state(config), timeout=5.0
            )
            if existing_state and existing_state.values and "tools" in existing_state.values:
                old_tools = existing_state.values["tools"] or []
                new_tools = initial_state["tools"] or []

                def _resolve_tool_name(t: Any) -> str:
                    if isinstance(t, dict):
                        fn = t.get("function")
                        if isinstance(fn, dict) and "name" in fn:
                            return fn["name"]
                        return t.get("name", "unknown")
                    return getattr(t, "name", "unknown")

                # Merge by name, prioritizing the newer definitions (RAG/fresh injection)
                merged = {_resolve_tool_name(t): t for t in old_tools}
                for t in new_tools:
                    merged[_resolve_tool_name(t)] = t

                initial_state["tools"] = list(merged.values())
                logger.info(f"[ GRAPH ] Merged {len(new_tools)} fresh tools with {len(old_tools)} existing tools (Total: {len(initial_state['tools'])})")
        except asyncio.TimeoutError:
            logger.warning("[ GRAPH ] aget_state timed out (>5s) — skipping tool merge, using fresh tools only.")
        except Exception as e:
            logger.warning(f"[ GRAPH ] Failed to merge tools from existing state: {e}")

    final_state = None

    # Buffer for the supervisor's streamed answer text. The supervisor interleaves
    # tool calls and prose across ReAct iterations, so we cannot stream its content
    # live without (a) rendering intermediate "thinking out loud" narration as ghost
    # answer bubbles and (b) leaking text from a message that also makes a tool call.
    # We accumulate per model-call and flush at on_chat_model_end only when the
    # finished message made NO tool calls (the genuine final answer).
    supervisor_answer_buf: List[str] = []

    input_data = initial_state
    if effective_thread_id:
        try:
            state_data = await graph.aget_state(config)
            pending = (state_data.values or {}).get("pending_approval") if state_data else None
            from src.core.orchestration import approval_store
            entry = approval_store.peek(pending.get("token")) if pending else None
            call = pending.get("tool_call") if pending else None
            if entry and entry.get("resolved") and call:
                # Replay only the approved atomic call, through the normal gate
                # and ToolNode. Sibling calls already completed must not repeat.
                replay = dict(call, id=f"approval-{uuid4()}")
                update = dict(initial_state)
                update["messages"] = list(initial_state.get("messages") or []) + [
                    AIMessage(content="", tool_calls=[replay])
                ]
                update["pending_approval"] = None
                update["final_response"] = None
                input_data = Command(update=update, goto="tools")
            elif state_data and state_data.next:
                logger.info(f"[ GRAPH ] Pending interrupt detected at {state_data.next}. Resuming graph...")
                # IMPORTANT: Only pass non-destructive fields on resume.
                # Do NOT pass planning fields (active_plan, plan_step, failure_context)
                # with None/empty values — that would wipe the plan saved by planner_node.
                # Only update message history, tools, metadata, and system context.
                resume_update = {
                    k: v for k, v in initial_state.items()
                    if k not in ("active_plan", "plan_step", "failure_context", "validation_feedback", "paused_for_input", "plan_replanned")
                    or v is not None
                }
                await graph.aupdate_state(config, resume_update)
                input_data = None
        except Exception as ex:
            logger.warning(f"[ GRAPH ] Failed to check state for resume: {ex}")

    timed_out = False

    # --- NATIVE EVENT STREAMING LOOP (Streaming v3) ---
    try:
        async for event in graph.astream_events(input_data, config=config, version="v2"):
            kind = event.get("event")

            # Approval pauses must checkpoint normally; never cancel the event
            # stream before the tools node and specialist handoff have finished.

            # 1. Capture standard LLM tokens emitted by the ChatChad wrapper.
            #    Only the supervisor, conversational, voice, and contact nodes produce
            #    user-facing answer text (tagged 'supervisor-llm' /
            #    'conversational-llm' / 'voice-llm' / 'contact-llm'). Every other model stream —
            #    subagents, planner, validator — is internal and must NOT reach the
            #    answer bubble (that was the source of subagent output leaking into
            #    the main response). The conversational, voice, and contact nodes stream live
            #    (voice/contact replies are short by prompt design, so any lead-in narration
            #    before a tool call is meant to be heard/seen); the supervisor is buffered
            #    and only flushed when it produced a final, tool-call-free answer
            #    (see #3).
            if kind == "on_chat_model_stream" and on_token:
                chunk = event["data"].get("chunk")
                if chunk and chunk.content:
                    tags = event.get("tags") or []
                    if "supervisor-llm" in tags:
                        supervisor_answer_buf.append(chunk.content)
                    elif (
                        "conversational-llm" in tags
                        or "voice-llm" in tags
                        or "contact-llm" in tags
                    ):
                        await _safe_call(on_token, chunk.content)
                    # else: internal stream (subagent/planner/validator) — suppress.

            # 2. Capture custom thinking blocks dispatched by ChatChad wrapper
            elif kind == 'on_custom_event' and event['name'] == 'browser_progress' and on_thinking:
                await _safe_call(on_thinking, json.dumps(event['data']), 'browser_progress', False,
                                 agent_name='Browser', parent_id=event['data'].get('parent_id'))
            elif kind == "on_custom_event" and event["name"] == "agent_thinking" and on_thinking:
                payload = event["data"]
                thinking_text = payload.get("thinking", "")
                thinking_type = payload.get("type") or "reasoning"

                # Ensure the type is a standard thinking type (not the agent name)
                if thinking_type not in ["reasoning", "thought", "thinking"]:
                    thinking_type = "reasoning"

                append = payload.get("append", True)
                if "complete" in payload:
                    if payload.get("complete", False):
                        append = False  # Completed thinking block, do not append further
                    else:
                        append = True   # Still streaming chunks, continue appending

                # Always forward agent_name from the ChatChad payload so Planner/Scout
                # limbs label correctly even without a parent_id (plan-lane mid-loop).
                parent_id = payload.get("parent_id")
                agent_name = payload.get("agent_name")

                # Suppress parallel research worker thoughts from the user-facing chat stream (#508).
                # Workers execute concurrently; their internal reasoning scratchpad interleaves
                # and floods the UI with 1-word fragments. Their tool calls (web_search) remain
                # fully visible to the user as action cards.
                if agent_name and str(agent_name).lower().startswith("research worker"):
                    continue

                await _safe_call(
                    on_thinking,
                    thinking_text,
                    thinking_type,
                    append,
                    agent_name=agent_name,
                    parent_id=parent_id,
                )

            # 3. Chat model finished: flush the supervisor's final answer (if any)
            #    and capture token usage.
            elif kind == "on_chat_model_end":
                output = event["data"].get("output")

                # Flush buffered supervisor answer ONLY when the finished message
                # made no tool calls — i.e. it is the genuine final answer, not the
                # narration the model emits alongside a delegate/tool call.
                if "supervisor-llm" in (event.get("tags") or []):
                    buffered = "".join(supervisor_answer_buf)
                    supervisor_answer_buf.clear()
                    has_tool_calls = bool(getattr(output, "tool_calls", None))
                    if buffered and not has_tool_calls and on_token:
                        await _safe_call(on_token, buffered)

                if output is not None and on_usage and hasattr(output, "response_metadata"):
                    usage = output.response_metadata.get("token_usage")
                    if usage:
                        await _safe_call(on_usage, usage)

            # 4. Continuously capture step/chain outputs.
            # The very last one returned will be the final graph state.
            elif kind == "on_chain_end":
                out = event["data"].get("output")
                if out and isinstance(out, dict) and "messages" in out:
                    final_state = out
    except NodeTimeoutError as timeout_err:
        timed_out = True
        logger.warning(
            f"[ GRAPH ] Node '{timeout_err.node}' timed out ({timeout_err.kind}, "
            f"{timeout_err.elapsed:.1f}s). Attempting partial state recovery."
        )
        if not final_state and effective_thread_id:
            try:
                state_data = await graph.aget_state(config)
                if state_data and state_data.values:
                    final_state = state_data.values.copy()
            except Exception as ex:
                logger.warning(f"[ GRAPH ] Failed to hydrate state after timeout: {ex}")
        if not final_state:
            final_state = initial_state.copy()
        if not final_state.get("final_response"):
            if timeout_err.kind == "idle":
                final_state["final_response"] = (
                    f"⚠️ This step took too long without a response (over {timeout_err.timeout:.0f}s idle). "
                    "This can happen during large code generation. "
                    "Try breaking the request into smaller tasks, or retry."
                )
            else:
                final_state["final_response"] = (
                    f"⚠️ This step exceeded the maximum allowed time ({timeout_err.timeout:.0f}s). "
                    "Try breaking the request into smaller tasks."
                )

    # Fallback State Hydration
    if not final_state:
        logger.warning("[ GRAPH ] Final state not captured from event stream. Hydrating from history.")
        if effective_thread_id:
            try:
                state_data = await graph.aget_state(config)
                if state_data and state_data.values:
                    final_state = state_data.values
            except Exception as ex:
                logger.error(f"[ GRAPH ] Failed fallback hydration: {ex}")

        if not final_state:
            final_state = initial_state

    # Detect graph interrupt (e.g. plan approval pause) for turn-level messaging.
    # Timeouts/errors are terminal and must never be treated as approval pauses.
    if effective_thread_id and not timed_out:
        try:
            state_data = await graph.aget_state(config)
            has_active_plan = bool(final_state.get("active_plan")) or (
                state_data and bool(state_data.values.get("active_plan"))
            )
            if state_data and state_data.next and has_active_plan and not final_state.get("pending_approval"):
                final_state["graph_interrupt_pending"] = True
        except Exception as ex:
            logger.debug(f"[ GRAPH ] Could not read interrupt state: {ex}")

    # --- FINAL RESPONSE EXTRACTION ---
    # If final_response wasn't explicitly set by a specialized node,
    # we extract the content of the last AIMessage that was NOT a tool call.
    # Skip when an interactive tool is waiting — the card carries the prompt.
    if not final_state.get("final_response") and not final_state.get("pending_approval"):
        messages = final_state.get("messages", [])
        if messages:
            from src.shared.plan_markers import extract_plan_from_text
            # Search backwards for the last assistant message
            for msg in reversed(messages):
                if isinstance(msg, AIMessage):
                    # If it has tool calls, it's not a final response message
                    if getattr(msg, "tool_calls", None):
                        continue

                    # If it has content, use it
                    if msg.content:
                        content = str(msg.content)

                        # Skip raw planner JSON — not user-facing speech.
                        _plan_blob, stripped_plan_text = extract_plan_from_text(content)
                        if _plan_blob and not stripped_plan_text.strip():
                            continue
                        if stripped_plan_text:
                            content = stripped_plan_text

                        # REFINEMENT: Aggressively strip internal plans and JSON artifacts
                        # 1. Remove JSON code blocks that look like plans (e.g. lists of steps)
                        content = re.sub(r"```json\s*\[\s*\{.*?\}\s*\]\s*```", "", content, flags=re.DOTALL)

                        # 3. Strip orchestration markers and terminal emojis
                        clean_content = re.sub(
                            r"(?:TASK_COMPLETED|TASK_FAILED|NEEDS_HUMAN|\[\[done\]\])\s*🦾?",
                            "",
                            content,
                            flags=re.IGNORECASE
                        ).strip()

                        if clean_content:
                            # Check for truncation
                            finish_reason = msg.additional_kwargs.get("finish_reason")
                            if finish_reason == "length":
                                clean_content += "\n\n⚠️ *[Response truncated due to model token limit. Some actions may not have been performed.]*"
                                logger.warning(f"[ GRAPH ] Detected truncated response in turn {final_state.get('turn_id')}")

                            final_state["final_response"] = clean_content
                            break

    return final_state
