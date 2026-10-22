"""Planner node — info-needs tools + propose_plan (no JSON content scrape)."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from src.core.intelligence.adapters.litellm_adapter import requires_legacy_nonstreaming_structured
from src.core.orchestration.context import await_with_progress_heartbeat
from src.core.orchestration.agent_scope import agent_scope
from src.core.orchestration.tool_lifecycle import run_with_tool_lifecycle
from src.core.orchestration.nodes.scout_node import (
    build_confirmed_tool_menu,
    validate_plan_tools_against_menu,
)
from src.core.orchestration.plan_service import PlanService
from src.tools.system.dispatch_scout import DispatchScoutTool
from src.tools.system.propose_plan import ProposePlanTool
from src.core.orchestration.scout_evidence import render_evidence

logger = logging.getLogger("ChadSupervisor")

# Never appear as execution-plan step tools — planning-phase / mid-run only.
_PLAN_STEP_FORBIDDEN_TOOLS = frozenset({
    "ask_user_question",
    "pause_for_input",
    "dispatch_scout",
    "skip_scout",
    "review_scout",
    "propose_plan",
    "switch_to_plan_mode",
})

def _propose_nudge_text() -> str:
    """Load nudge from prompts/persona (nodes must not own prompt prose)."""
    from pathlib import Path

    for parent in Path(__file__).resolve().parents:
        p = parent / "prompts" / "persona" / "planner_propose_nudge.md"
        if p.is_file():
            body = p.read_text(encoding="utf-8")
            if body.startswith("---"):
                parts = body.split("---", 2)
                body = parts[2] if len(parts) >= 3 else body
            return body.strip()
    return (
        "You have enough to plan. Call `propose_plan` now with your concrete steps. "
        "Do not write JSON in the message — use the tool."
    )


def _planner_thinking_level(model: Any) -> str:
    """Model-specific planner thinking level (medium allows deep reasoning up to the runaway cap)."""
    return "medium"


def _strip_forbidden_plan_tools(plan_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop planning-phase tools from step tool lists (they are never executable steps)."""
    cleaned: List[Dict[str, Any]] = []
    for step in plan_list:
        if not isinstance(step, dict):
            continue
        tools = [
            t for t in (step.get("tools") or [])
            if t and t not in _PLAN_STEP_FORBIDDEN_TOOLS
        ]
        step = dict(step)
        step["tools"] = tools
        cleaned.append(step)
    return cleaned


def _normalize_plan_steps(plan_list: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Coerce a parsed plan into dict steps only. Never leave strings/ints in the list."""
    normalized: List[Dict[str, Any]] = []
    for i, step in enumerate(plan_list or []):
        if not isinstance(step, dict):
            logger.warning("[ PLANNER ] Skipping non-dict plan step at index %d: %r", i, step)
            continue
        step = dict(step)
        if "id" not in step:
            step["id"] = f"step_{i + 1}"
        # A proposed plan has not executed yet. Model-supplied aliases such as
        # 'in_progress' otherwise survive and are invisible to execution routing.
        step["status"] = "pending"
        if "description" not in step:
            step["description"] = "No description provided"
        if "tools" not in step:
            step["tools"] = []
        step.pop("assigned_to", None)
        step.pop("expected_output", None)
        normalized.append(step)
    return normalized


def _first_step_id(plan_list: List[Dict[str, Any]]) -> str:
    if not plan_list:
        return ""
    step = plan_list[0]
    if isinstance(step, dict):
        return str(step.get("id") or "")
    return ""


def _serialize_tool_result(result: Any) -> str:
    import json

    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return str(result)


def _build_review_scout_tool(can_follow_up: bool) -> StructuredTool:
    async def _review(decision: str, reason: str, brief: str = "") -> Dict[str, Any]:
        if not reason.strip() or decision not in {"proceed", "follow_up"}:
            return {"success": False, "error": "Choose proceed or follow_up and explain coverage of the requested facts and remaining gaps."}
        if decision == "follow_up":
            if not can_follow_up or not brief.strip():
                return {"success": False, "error": "Follow-up requires a targeted nonempty brief and an available Scout round. Otherwise proceed with explicit gaps or clarify after review."}
            return {"success": True, "_dispatch_scout": True, "brief": brief.strip()}
        return {"success": True, "_scout_coverage": True, "reason": reason.strip()}
    return StructuredTool.from_function(coroutine=_review, name="review_scout",
        description="Compare Scout findings against the gather brief. Proceed with an explicit assessment of facts/gaps, or request one targeted follow-up for unresolved items. Do not restart completed research.")


def _build_skip_scout_tool() -> StructuredTool:
    async def _skip(reason: str) -> Dict[str, Any]:
        if not reason.strip():
            return {"success": False, "error": "A non-empty reason is required to skip Scout."}
        return {"success": True, "_skip_scout": True, "reason": reason.strip()}

    return StructuredTool.from_function(
        coroutine=_skip, name="skip_scout",
        description="Explicitly skip reconnaissance only when no missing external facts are needed. Give a reason.",
    )


def _build_dispatch_scout_tool() -> StructuredTool:
    native = DispatchScoutTool()

    async def _run(brief: str) -> Dict[str, Any]:
        return await native.execute(brief=brief)

    return StructuredTool.from_function(
        name=native.name,
        description=native.description,
        coroutine=_run,
    )


def _build_propose_plan_tool() -> StructuredTool:
    """Bind propose_plan with a flat JSON schema (no $defs/$ref).

    Nested Pydantic models emit ``#/$defs/_PlanStep`` refs. LM Studio / Gemma
    reject those with ``JSON schema conversion failed``, so the tool never
    binds and the planner degrades to a 1-step fallback.
    """
    from pydantic import BaseModel, Field, GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue

    native = ProposePlanTool()
    flat_params = native.get_schema()["parameters"]

    class _ProposePlanArgs(BaseModel):
        steps: List[Dict[str, Any]] = Field(
            description="Ordered execution steps for the supervisor after approval."
        )

        @classmethod
        def __get_pydantic_json_schema__(
            cls, core_schema: Any, handler: GetJsonSchemaHandler
        ) -> JsonSchemaValue:
            return {
                "type": "object",
                "title": "_ProposePlanArgs",
                "properties": flat_params["properties"],
                "required": list(flat_params.get("required") or ["steps"]),
            }

    async def _run(steps: List[Any]) -> Dict[str, Any]:
        coerced: List[Dict[str, Any]] = []
        for step in steps or []:
            if hasattr(step, "model_dump"):
                coerced.append(step.model_dump())
            elif isinstance(step, dict):
                coerced.append(step)
        return await native.execute(steps=coerced)

    return StructuredTool.from_function(
        name=native.name,
        description=native.description,
        coroutine=_run,
        args_schema=_ProposePlanArgs,
    )


def _finish_with_plan(
    state: Dict[str, Any],
    *,
    plan_list: List[Dict[str, Any]],
    menu: List[Dict[str, str]],
    messages: List[Any],
    gather_brief: str,
    failure: Any,
    feedback: Any,
) -> Dict[str, Any]:
    """Normalize proposed steps and emit the approval snapshot."""
    plan_list = _strip_forbidden_plan_tools(_normalize_plan_steps(plan_list))
    plan_goal = PlanService.resolve_goal(state)

    existing_plan = [
        s for s in (state.get("active_plan") or []) if isinstance(s, dict)
    ]
    had_active_plan = bool(existing_plan)
    is_autonomous_replan = bool(failure or feedback or had_active_plan)

    if not plan_list:
        if existing_plan:
            logger.error(
                "[ PLANNER ] Empty propose_plan; keeping existing %d-step plan.",
                len(existing_plan),
            )
            PlanService.emit_snapshot(
                state, existing_plan, goal=plan_goal, gather_brief=gather_brief or None,
            )
            return {
                "active_plan": existing_plan,
                "plan_step": _first_step_id(existing_plan),
                "plan_goal": plan_goal,
                "failure_context": None,
                "validation_feedback": None,
                "plan_replanned": True,
                "messages": messages,
                "confirmed_tool_menu": menu,
                "scout_decision": state.get("scout_decision"),
                "scout_coverage": state.get("scout_coverage"),
                "scout_gather_request": None,
                "pending_approval": None,
            }
        desc = plan_goal or "Complete the user's request"
        logger.error("[ PLANNER ] Empty propose_plan; falling back to single-step plan.")
        plan_list = [
            {"id": "step_1", "description": desc, "tools": [], "status": "pending"}
        ]

    # Auto-assign tools from menu if step description explicitly demands them and model left tools empty
    menu_names = {e.get("name") for e in (menu or []) if e.get("name")}
    for step in plan_list:
        if not step.get("tools"):
            desc_lower = str(step.get("description", "")).lower()
            inferred = []
            if any(w in desc_lower for w in ["write", "save", "markdown report", "to artifacts/", "to file", "document"]) and "write_file" in menu_names:
                inferred.append("write_file")
            if any(w in desc_lower for w in ["pytest", "terminal", "shell", "command", "bash"]) and "run_host_command" in menu_names:
                inferred.append("run_host_command")
            if any(w in desc_lower for w in ["gmail draft", "draft email", "draft addressed"]) and "create_gmail_draft" in menu_names:
                inferred.append("create_gmail_draft")
            if any(w in desc_lower for w in ["chart", "plot", "graph"]) and "render_chart" in menu_names:
                inferred.append("render_chart")
            if any(w in desc_lower for w in ["search the web", "web search", "google"]) and "web_search" in menu_names:
                inferred.append("web_search")
            if inferred:
                step["tools"] = inferred
                logger.info("[ PLANNER ] Inferred missing tools %s for step '%s'", inferred, step.get("id"))

    from src.shared.config_manager import ConfigManager

    validation_enabled = bool(ConfigManager.get("planner.validation_enabled", False))
    tool_validation = (
        validate_plan_tools_against_menu(plan_list, menu)
        if validation_enabled
        else None
    )
    if tool_validation and not is_autonomous_replan:
        logger.warning("[ PLANNER ] Plan tool validation failed: %s", tool_validation)
        return {
            "validation_feedback": tool_validation,
            "messages": messages,
            "confirmed_tool_menu": menu,
            "scout_decision": state.get("scout_decision"),
            "scout_coverage": state.get("scout_coverage"),
            "scout_gather_request": None,
        }

    PlanService.emit_snapshot(
        state, plan_list, goal=plan_goal, gather_brief=gather_brief or None,
    )
    if is_autonomous_replan:
        logger.info(
            "[ PLANNER ] Autonomous replan complete (%d steps). Continuing without approval pause.",
            len(plan_list),
        )

    return {
        "active_plan": plan_list,
        "plan_step": _first_step_id(plan_list),
        "plan_goal": plan_goal,
        "failure_context": None,
        "validation_feedback": None,
        "plan_replanned": is_autonomous_replan,
        "messages": messages,
        "confirmed_tool_menu": menu,
        "scout_decision": state.get("scout_decision"),
        "scout_coverage": state.get("scout_coverage"),
        "scout_gather_request": None,
        "pending_approval": None,
    }


async def _run_planner_tool_loop(
    model: Any,
    state: Dict[str, Any],
    config: RunnableConfig,
    *,
    tool_registry: Any,
    planning_base: str,
    menu: List[Dict[str, str]],
    allow_ask: bool = True,
    allow_dispatch: bool = True,
) -> Dict[str, Any]:
    """
    Planner tool loop: ask / dispatch_scout / propose_plan.

    Scout dispatch or an explicit skip must precede planning. propose_plan is then bound. Returns pending_approval, scout_gather_request,
    or a finished plan payload from _finish_with_plan.
    """
    from src.shared.config_manager import ConfigManager

    gather_brief = (state.get("gather_brief") or "").strip()
    dispatch_count = int(state.get("scout_dispatch_count") or 0)
    max_dispatch = min(2, max(1, int(ConfigManager.get("planner.max_scout_dispatches", 2) or 2)))
    max_rounds = int(ConfigManager.get("planner.info_needs_max_rounds", 6) or 6)

    state = dict(state)
    decision_required = not (gather_brief or dispatch_count or state.get("scout_decision"))
    coverage_required = bool(state.get("scout_evidence")) and not state.get("scout_coverage")
    propose_tool = _build_propose_plan_tool()
    planning_tools: List[Any] = [propose_tool]
    if allow_ask and tool_registry is not None:
        ask_tool = tool_registry.get_langchain_tool_by_name("ask_user_question")
        if ask_tool is not None:
            planning_tools.append(ask_tool)
    decision_tools: List[Any] = [_build_skip_scout_tool()]
    if allow_dispatch and dispatch_count < max_dispatch:
        decision_tools.insert(0, _build_dispatch_scout_tool())
    coverage_tools = [_build_review_scout_tool(allow_dispatch and dispatch_count < max_dispatch)]
    bound = decision_tools if decision_required else coverage_tools if coverage_required else planning_tools

    tool_map = {getattr(t, "name", ""): t for t in bound if getattr(t, "name", None)}

    # Runtime data only — doctrine lives in prompts/persona/planner.md
    prompt = planning_base
    prompt += (
        f"\n\n### RUNTIME\n"
        f"- scout_decision_required={decision_required}\n"
        f"- scout_coverage_required={coverage_required}\n"
        f"- allow_ask={allow_ask and not decision_required}\n"
        f"- scout_dispatches_used={dispatch_count}/{max_dispatch}\n"
        f"- dispatch_scout_bound={decision_required and allow_dispatch and dispatch_count < max_dispatch}\n"
    )
    evidence_context = render_evidence(state.get("scout_evidence"))
    if evidence_context:
        prompt += evidence_context + "\nAssess the requested facts and gaps with the currently bound tool before planning.\n"
    elif gather_brief:
        prompt += f"\n### SCOUT GATHER BRIEF\n{gather_brief}\n"
    else:
        prompt += "\n### SCOUT GATHER BRIEF\n(none yet)\n"
    if menu:
        menu_lines = "\n".join(
            f"- `{e.get('name')}`: {e.get('description', '')}" for e in menu
        )
        prompt += f"\n### CONFIRMED TOOL MENU\n{menu_lines}\n"

    history = list(state.get("messages") or [])
    if gather_brief and history:
        import json
        updated_history = []
        for msg in history:
            if isinstance(msg, ToolMessage) and (
                getattr(msg, "name", "") in ("dispatch_scout", "base_tool")
                or "Scout dispatched" in str(msg.content)
            ):
                updated_history.append(
                    ToolMessage(
                        content=json.dumps({"success": True, "result": "See SCOUT EVIDENCE for findings and gaps."} if evidence_context else {"success": True, "gather_brief": gather_brief, "result": gather_brief}),
                        tool_call_id=msg.tool_call_id,
                        name=getattr(msg, "name", "dispatch_scout"),
                    )
                )
            else:
                updated_history.append(msg)
        history = updated_history
    messages: List[Any] = [SystemMessage(content=prompt)] + history
    thinking_level = _planner_thinking_level(model)
    planner_model = model.copy(update={"thinking_level": thinking_level, "max_tokens": 4096}).bind_tools(bound)

    new_messages: List[Any] = []
    nudged = False
    for _round in range(max_rounds):
        response = await await_with_progress_heartbeat(
            planner_model.ainvoke(messages, config=config)
        )
        new_messages.append(response)
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            if not nudged:
                nudged = True
                nudge = ("Choose exactly one tool: dispatch_scout(brief) or skip_scout(reason)."
                         if decision_required else "Call review_scout to assess findings and gaps."
                         if coverage_required else _propose_nudge_text())
                messages.append(HumanMessage(content=nudge))
                new_messages.append(messages[-1])
                continue
            if decision_required or coverage_required:
                raise RuntimeError("Planner did not choose dispatch_scout or skip_scout, or complete Scout coverage review; no plan was created.")
            logger.error("[ PLANNER ] No propose_plan after nudge — degrading.")
            return _finish_with_plan(
                state,
                plan_list=[],
                menu=menu,
                messages=new_messages,
                gather_brief=gather_brief,
                failure=state.get("failure_context"),
                feedback=state.get("validation_feedback"),
            )

        if (decision_required or coverage_required) and (len(tool_calls) != 1 or tool_calls[0].get("name") not in tool_map):
            # Reject the whole batch: a mixed dispatch+proposal must never skip recon.
            for tc in tool_calls:
                error = ToolMessage(
                    content=f"Complete the required phase with exactly one of: {', '.join(tool_map)}. Planning is not yet available.",
                    tool_call_id=tc.get("id", tc.get("name", "invalid")), name=tc.get("name", "invalid"),
                )
                messages.append(error)
                new_messages.append(error)
            continue

        # Prefer propose_plan if present in a multi-tool turn.
        ordered = sorted(
            tool_calls,
            key=lambda tc: 0 if (tc.get("name") or "") == "propose_plan" else 1,
        )
        for tc in ordered:
            name = tc.get("name") or ""
            args = tc.get("args") or {}
            tool = tool_map.get(name)
            if not tool:
                raw: Any = {"success": False, "error": f"Tool '{name}' not bound for planner."}
            else:
                async def _run():
                    if hasattr(tool, "ainvoke"):
                        return await tool.ainvoke(args, config=config)
                    return await tool.coroutine(**args)

                try:
                    if name == "dispatch_scout":
                        # Scout owns the dispatch lifecycle until gathering finishes.
                        # Completing it here produces an empty, already-done UI panel.
                        raw = await _run()
                    else:
                        raw = await run_with_tool_lifecycle(name, args, _run)
                except Exception as exc:
                    logger.warning("[ PLANNER ] Tool %s failed: %s", name, exc)
                    raw = {"success": False, "error": str(exc)}

            tm = ToolMessage(
                content=_serialize_tool_result(raw),
                tool_call_id=tc.get("id", name),
                name=name,
            )
            new_messages.append(tm)
            messages.append(tm)

            if isinstance(raw, dict) and raw.get("_scout_coverage"):
                state["scout_coverage"] = raw["reason"]
                coverage_required = False
                messages[0] = SystemMessage(content=prompt.replace("scout_coverage_required=True", "scout_coverage_required=False"))
                bound = planning_tools
                tool_map = {t.name: t for t in bound}
                planner_model = model.copy(update={"thinking_level": thinking_level, "max_tokens": 4096}).bind_tools(bound)
                messages.append(HumanMessage(content="Coverage assessment recorded. Propose an executable plan that preserves unresolved gaps, or ask an essential clarification."))
                continue

            if isinstance(raw, dict) and raw.get("_skip_scout"):
                state["scout_decision"] = "skipped: " + raw["reason"]
                decision_required = False
                messages[0] = SystemMessage(content=prompt.replace(
                    "scout_decision_required=True", "scout_decision_required=False"
                ).replace("dispatch_scout_bound=True", "dispatch_scout_bound=False").replace(
                    "allow_ask=False", f"allow_ask={allow_ask}"
                ))
                bound = planning_tools
                tool_map = {t.name: t for t in bound}
                planner_model = model.copy(update={"thinking_level": thinking_level, "max_tokens": 4096}).bind_tools(bound)
                messages.append(HumanMessage(content="Scout explicitly skipped. You may now propose the plan or ask a necessary clarification."))
                continue

            if isinstance(raw, dict) and raw.get("_propose_plan"):
                steps = raw.get("steps") or args.get("steps") or []
                return _finish_with_plan(
                    state,
                    plan_list=list(steps) if isinstance(steps, list) else [],
                    menu=menu,
                    messages=new_messages,
                    gather_brief=gather_brief,
                    failure=state.get("failure_context"),
                    feedback=state.get("validation_feedback"),
                )

            if isinstance(raw, dict) and raw.get("_pending_approval"):
                from src.core.orchestration.context import pending_approval_var

                pending = pending_approval_var.get() or {
                    "token": raw.get("approval_token"),
                    "tool_name": name,
                    "decision_type": raw.get("decision_type"),
                    "prompt": raw.get("result"),
                    "options": raw.get("options"),
                    "title": raw.get("title"),
                    "questions": raw.get("questions"),
                }
                return {
                    "pending_approval": pending,
                    "messages": new_messages,
                    "active_plan": None,
                    "scout_gather_request": None,
                    "confirmed_tool_menu": menu,
                    "scout_decision": state.get("scout_decision"),
                    "scout_coverage": state.get("scout_coverage"),
                }

            if isinstance(raw, dict) and raw.get("_dispatch_scout"):
                brief = str(raw.get("brief") or args.get("brief") or "").strip()
                if not brief:
                    continue
                state["scout_decision"] = "dispatched"
                state["scout_coverage"] = None
                return {
                    "scout_gather_request": brief,
                    "scout_correlation_id": tc.get("id") or f"scout_{uuid.uuid4().hex}",
                    "scout_dispatch_count": dispatch_count + 1,
                    "messages": new_messages,
                    "active_plan": None,
                    "pending_approval": None,
                    "confirmed_tool_menu": menu,
                    "scout_decision": state.get("scout_decision"),
                    "scout_coverage": state.get("scout_coverage"),
                }

    if decision_required or coverage_required:
        raise RuntimeError("Planner exhausted its decision budget without completing the Scout decision/coverage review.")
    logger.warning("[ PLANNER ] Tool-loop round cap (%d) — degrading without propose_plan.", max_rounds)
    return _finish_with_plan(
        state,
        plan_list=[],
        menu=menu,
        messages=new_messages,
        gather_brief=gather_brief,
        failure=state.get("failure_context"),
        feedback=state.get("validation_feedback"),
    )


def make_planner_node(model: Any, tool_registry: Any = None, handoff_tools: Optional[List[Any]] = None) -> Callable:
    """Factory creating the planner node with the given LLM model."""

    async def planner_node(state: Dict[str, Any], config: RunnableConfig):
        """Ask / Scout / propose_plan until a plan is submitted."""
        with agent_scope("Planner"):
            return await _planner_node_impl(state, config)

    async def _planner_node_impl(state: Dict[str, Any], config: RunnableConfig):
        episode_id = state.get("turn_id") or state.get("session_id")
        has_open_plan = any(step.get("status") in {"pending", "running", "failed"} for step in (state.get("active_plan") or []))
        new_episode = bool(episode_id and state.get("scout_episode_id") != episode_id and not has_open_plan
                           and not state.get("failure_context") and not state.get("validation_feedback"))
        if new_episode:
            state = dict(state)
            state.update(scout_evidence=[], scout_coverage=None, gather_brief=None,
                         scout_decision=None, scout_dispatch_count=0)
        # Stored in every planner result, including dispatch and final plan.
        state = dict(state)
        state["scout_episode_id"] = episode_id
        failure = state.get("failure_context")
        feedback = state.get("validation_feedback")

        from src.core.intelligence.prompt_builder import SystemPromptBuilder

        planning_instructions = SystemPromptBuilder.build(base_instruction="", scope="planner")
        if failure:
            planning_instructions += (
                f"\n\n⚠️ RE-PLANNING REQUIRED due to task failure:\n{failure}\n"
                "Adjust the remaining steps of the plan accordingly to bypass/resolve this issue."
            )
        if feedback:
            planning_instructions += (
                f"\n\n⚠️ RE-PLANNING REQUIRED due to validation critique:\n{feedback}\n"
                "Modify or add steps to address the validation comments."
            )

        handoffs = handoff_tools or []
        menu = build_confirmed_tool_menu(
            state.get("tools") or [], handoffs
        )

        # Tool-name validation feedback: propose_plan only (no mid-run ask/dispatch).
        allow_ask = not bool(failure)
        # Prevent question loops: if ask_user_question was already answered in this turn/session,
        # latch allow_ask to False so the planner is forced to propose the plan.
        history_msgs = state.get("messages") or []
        if any(getattr(m, "name", "") == "ask_user_question" or (isinstance(m, dict) and m.get("name") == "ask_user_question") for m in history_msgs):
            allow_ask = False

        # If user explicitly forbade questions or asked for direct planning / best judgment,
        # latch allow_ask to False so the planner never violates user constraint.
        user_text = ""
        for m in history_msgs:
            c = getattr(m, "content", None)
            if isinstance(c, str):
                user_text += " " + c.lower()
            elif isinstance(m, dict) and isinstance(m.get("content"), str):
                user_text += " " + m.get("content", "").lower()

        no_question_phrases = (
            "do not ask",
            "no clarifying questions",
            "no questions",
            "don't ask",
            "best judgment",
            "best judgement",
            "proceed directly",
        )
        if any(phrase in user_text for phrase in no_question_phrases):
            allow_ask = False
        allow_dispatch = not bool(feedback and not failure)

        # Local name — assigning to the closure param would make it UnboundLocal.
        registry = tool_registry
        if registry is None:
            # Planner-owned decision/proposal tools do not require the external registry.
            from types import SimpleNamespace

            registry = SimpleNamespace(get_langchain_tool_by_name=lambda _n: None)

        result = await _run_planner_tool_loop(
            model,
            state,
            config,
            tool_registry=registry,
            planning_base=planning_instructions,
            menu=menu,
            allow_ask=allow_ask,
            allow_dispatch=allow_dispatch,
        )
        if episode_id:
            result["scout_episode_id"] = episode_id
        if new_episode:
            result.setdefault("scout_evidence", [])
            result.setdefault("scout_coverage", None)
            result.setdefault("gather_brief", None)
            result.setdefault("scout_dispatch_count", 0)
        return result

    return planner_node
