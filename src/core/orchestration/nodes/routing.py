import logging
from typing import Dict, Any, Callable
from langchain_core.messages import HumanMessage, AIMessage

from src.core.tools import limits

logger = logging.getLogger("ChadSupervisor")


def _planner_enabled() -> bool:
    """Master switch for the multi-step plan lane (config: planner.enabled).

    Defaults to False so the still-maturing plan lane stays off unless a deployment
    explicitly opts in. Lazy import keeps this module import-light.
    """
    from src.shared.config_manager import ConfigManager
    return bool(ConfigManager.get("planner.enabled", False))


def route_entry(state: Dict[str, Any]) -> str:
    category = state.get("category", "autonomous")
    turn_mode = state.get("turn_mode", "agent")

    if category == "voice":
        logger.info("[ ROUTE ] Voice category — fast voice responder.")
        return "voice_responder"

    if category == "contact":
        logger.info("[ ROUTE ] Contact category — Telegram 9B front desk.")
        return "contact_responder"

    # A plan is active AND still has unfinished steps — continue execution via the supervisor
    # even if this follow-up turn still carries category="planning"/turn_mode="plan" (that tag
    # is sticky for the whole conversation in both the GUI and evals/live.py's probe_workflow,
    # not just the turn that proposed the plan). Checking this BEFORE the plan-lane branch below
    # stops every "proceed"/"keep going" follow-up from re-entering Planner (and optional Scout)
    # from scratch mid-execution. That re-entry is wasteful (re-runs gather for no reason) and
    # was an observed source of mid-stream LiteLLM connection drops: Scout's explicit main-model
    # release (see scout_node._maybe_unload_main_model) raced the very next inference call's JIT
    # reload when it fired unexpectedly mid-execution instead of once at the true start of a
    # plan-lane turn.
    active_plan = [s for s in (state.get("active_plan") or []) if isinstance(s, dict)]
    if active_plan and any(s.get("status") in ("pending", "failed", "running") for s in active_plan):
        logger.info("[ ROUTE ] Active plan with pending steps — continuing execution via supervisor.")
        return "supervisor"

    if turn_mode == "plan" or category == "planning":
        if not _planner_enabled():
            logger.info(
                "[ ROUTE ] Plan lane requested but planner.enabled=false — "
                "falling back to agent lane (supervisor)."
            )
            return "supervisor"
        # Planner-directed Scout: planner owns info-needs and optionally dispatches Scout
        # via scout_gather_request — do NOT auto-scout on entry. (Restored after merge
        # c2cd2634 kept scout-first routing while landing planner-directed tests/nodes.)
        logger.info("[ ROUTE ] Plan lane — routing to planner (info-needs, then optional Scout).")
        return "planner"

    if turn_mode == "chat":
        logger.info("[ ROUTE ] Conversation lane — fast responder.")
        return "conversational"

    if state.get("active_plan"):
        logger.info("[ ROUTE ] Active plan detected. Routing to supervisor.")
        return "supervisor"

    logger.info("[ ROUTE ] Agent lane — routing to supervisor.")
    return "supervisor"


def route_after_scout(state: Dict[str, Any]) -> str:
    # Scout is gather-only and must never ask the user; if pending_approval somehow
    # appears, end the turn rather than looping. Normal path: return facts to planner.
    if state.get("pending_approval"):
        logger.info("[ ROUTE ] Scout paused for user input (unexpected). Ending turn.")
        return "__end__"
    logger.info("[ ROUTE ] Scout gather complete. Returning to planner.")
    return "planner"


def route_after_planner(state: Dict[str, Any]) -> str:
    if state.get("pending_approval"):
        logger.info("[ ROUTE ] Planner clarifying with user. Ending turn.")
        return "__end__"

    if state.get("scout_gather_request") and not state.get("active_plan"):
        logger.info("[ ROUTE ] Planner dispatched Scout gather. Routing to scout.")
        return "scout"

    if state.get("validation_feedback") and not state.get("active_plan"):
        # Tool-name validation failure: the gathered facts are unchanged — only the plan's
        # tool names were wrong. Loop straight back to the planner with the feedback rather
        # than re-running the (expensive) scout gather.
        logger.warning("[ ROUTE ] Planner tool validation failed. Re-planning (no re-scout).")
        return "planner"
    if state.get("plan_replanned"):
        logger.info("[ ROUTE ] Autonomous replan complete. Routing to supervisor (no approval pause).")
        return "supervisor"

    messages = state.get("messages", [])
    last_human = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_human = msg
            break
        elif isinstance(msg, AIMessage):
            break

    if last_human:
        content = str(last_human.content).lower().strip()
        if any(w in content for w in ["change", "modify", "instead", "no ", "don't", "dont", "different"]):
            logger.info(
                f"[ ROUTE ] User requested plan revision: '{content[:50]}...'. Routing to planner."
            )
            return "planner"

    if not state.get("active_plan"):
        # Info-needs finished without a plan (shouldn't happen often) — end rather than approve empty.
        logger.warning("[ ROUTE ] Planner finished without an active plan. Ending turn.")
        return "__end__"

    logger.info("[ ROUTE ] Plan ready — pausing for user approval before execution.")
    return "approval_wait"


def route_after_approval_wait(state: Dict[str, Any]) -> str:
    """After the user responds to a plan approval pause, execute or revise."""
    messages = state.get("messages", [])
    last_human = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_human = msg
            break
        elif isinstance(msg, AIMessage):
            break

    if last_human:
        content = str(last_human.content).lower().strip()
        if any(w in content for w in ["change", "modify", "instead", "no ", "don't", "dont", "different"]):
            logger.info(
                f"[ ROUTE ] User requested plan revision after approval pause: "
                f"'{content[:50]}...'. Routing to planner."
            )
            return "planner"

    logger.info("[ ROUTE ] Plan accepted. Routing to supervisor.")
    return "supervisor"


def route_after_validator(state: Dict[str, Any]) -> str:
    if state.get("validation_feedback"):
        logger.warning("[ ROUTE ] Validation failed. Routing back to planner for re-plan.")
        return "planner"
    return "__end__"


def make_route_after_supervisor(sub_agents_dict: dict) -> Callable[[Dict[str, Any]], str]:
    """Factory creating the route_after_supervisor router closure."""
    
    def route_after_supervisor(state: Dict[str, Any]) -> str:
        """Determines next node after supervisor."""
        last_message = state["messages"][-1]
        _tc = getattr(last_message, "tool_calls", None)
        if _tc:
            def _fmt_call(tc: dict) -> str:
                name = tc.get("name", "unknown")
                args = tc.get("args") or {}
                args_preview = str(args)
                if len(args_preview) > 90:
                    args_preview = args_preview[:87] + "..."
                return f"{name}({args_preview})"
            calls_str = ", ".join(_fmt_call(c) for c in _tc)
            logger.info(f"[ ROUTE ] Supervisor emitted {len(_tc)} tool call(s): {calls_str}")
        else:
            logger.info(f"[ ROUTE ] route_after_supervisor: last_msg type={type(last_message).__name__}, active_plan={bool(state.get('active_plan'))}")

        # Runway ceiling. Deliberately NOT a silent kill any more: the supervisor
        # gets a "land the plane" directive from supervisor_node once it passes the
        # soft threshold, and only stops here if it ignored that too.
        #
        # The old value (25, hardcoded) was also arithmetically unreachable
        # alongside the per-step budget: one plan step may legitimately use 20
        # tool-turns, so a two-step plan could never finish — it died mid-step 2
        # with no message, looking like the model gave up. See limits.py.
        turns_max = int(limits.get("supervisor_turns_max"))
        if state.get("iteration_count", 0) >= turns_max:
            logger.warning(
                "[ GUARD ] Supervisor turn ceiling (%d) reached after the land-the-plane "
                "warning. Ending turn.", turns_max,
            )
            return "__end__"

        # Step stall / fail_step: recover on the same plan via supervisor with
        # actionable failure_context — do NOT scout+replan (that wiped good progress).
        if state.get("failure_context"):
            if last_message.tool_calls:
                logger.info("[ ROUTE ] failure_context set but tool_calls present — running tools first.")
                return "tools"
            logger.warning(
                "[ ROUTE ] failure_context set — recovering via supervisor "
                "(different approach, no scout/replan)."
            )
            return "supervisor"

        plan = state.get("active_plan") or []

        if last_message.tool_calls:
            delegate_agent_names = []
            for tc in last_message.tool_calls:
                tool_name = tc["name"]

                if tool_name == "create_plan":
                    # Mid-execution create_plan still goes to planner; planner_node marks
                    # plan_replanned so we skip a second approval_wait (see planner_node).
                    if state.get("active_plan"):
                        logger.info(
                            "[ ROUTE ] 'create_plan' during active plan — autonomous replan "
                            "(no second approval pause)."
                        )
                    else:
                        logger.info("[ ROUTE ] 'create_plan' tool called. Routing to planner.")
                    return "planner"

                if tool_name == "pause_for_input":
                    logger.info("[ ROUTE ] 'pause_for_input' called. Pausing execution for user input.")
                    return "tools"  # run the tool so the message is returned, then end

                if tool_name.startswith("delegate_to_"):
                    agent_name = tool_name.replace("delegate_to_", "")
                    if agent_name in sub_agents_dict and agent_name not in delegate_agent_names:
                        delegate_agent_names.append(agent_name)

            # #513: more than one independent delegate call in the same turn — fan out via
            # the parallel_delegate node (VRAM-gated: it decides per-pair whether targets
            # actually run concurrently or fall back to serial). Exactly one delegate call
            # keeps today's behavior unchanged (straight to that agent's own node).
            if len(delegate_agent_names) > 1:
                logger.info(
                    f"[ ROUTE ] {len(delegate_agent_names)} delegate calls in one turn "
                    f"{delegate_agent_names} — routing to parallel_delegate."
                )
                return "parallel_delegate"
            if len(delegate_agent_names) == 1:
                logger.info(f"[ ROUTE ] Directing intent to specialist: '{delegate_agent_names[0]}'")
                return delegate_agent_names[0]
            return "tools"

        if plan:
            pending_steps = [
                s for s in plan
                if isinstance(s, dict) and s.get("status") in ["pending", "failed", "running"]
            ]
            if not pending_steps:
                from src.shared.config_manager import ConfigManager
                if not ConfigManager.get("planner.validation_enabled", False):
                    logger.info("[ ROUTE ] All plan steps complete. Validator disabled — ending turn.")
                    return "__end__"
                logger.info("[ ROUTE ] All plan steps complete. Routing to validator.")
                return "validator"
            else:
                logger.warning("[ ROUTE ] Plan has pending steps but supervisor made no tool calls. Yielding control to user.")
                return "__end__"

        return "__end__"
        
    return route_after_supervisor


def route_after_voice_responder(state: Dict[str, Any]) -> str:
    """Voice lane: tools only for hubspace or escalate; no supervisor loop."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "__end__"


def route_after_contact_responder(state: Dict[str, Any]) -> str:
    """Contact front desk: escalate_to_agent only; no supervisor loop."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "__end__"


def route_after_conversational_responder(state: Dict[str, Any]) -> str:
    """Chat lane: read tools allowed; single-hop, no supervisor loop."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "__end__"


def route_after_tools(state: Dict[str, Any]) -> str:
    if state.get("pending_approval"):
        logger.info("[ ROUTE ] pending_approval set (interactive tool). Exiting to user.")
        return "__end__"
    if state.get("paused_for_input"):
        logger.info("[ ROUTE ] paused_for_input flag set. Exiting to user.")
        return "__end__"
    if state.get("category") == "voice":
        if state.get("voice_escalate"):
            logger.info("[ ROUTE ] Voice escalate flag set. Ending voice turn for agent handoff.")
            return "__end__"
        rounds = state.get("voice_tool_rounds", 0)
        if rounds < 1:
            logger.info("[ ROUTE ] Voice tool done — one follow-up for spoken summary.")
            return "voice_responder"
        return "__end__"
    if state.get("category") == "contact":
        if state.get("voice_escalate"):
            logger.info("[ ROUTE ] Contact escalate flag set. Ending contact turn for agent handoff.")
            return "__end__"
        return "__end__"
    if state.get("turn_mode") == "chat" or state.get("category") == "chat":
        rounds = state.get("chat_tool_rounds", 0)
        if rounds <= 1:
            logger.info("[ ROUTE ] Chat tool done — follow-up for conversational answer.")
            return "conversational_responder"
        return "__end__"
    return "supervisor"
