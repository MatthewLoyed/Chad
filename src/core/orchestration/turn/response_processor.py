from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.shared.logger import setup_logger
from src.shared.plan_markers import extract_plan_from_text, strip_orchestration_markers
from src.core.orchestration.turn import metrics as turn_metrics

logger = setup_logger("TurnResponsePostProcessor")


@dataclass
class TurnOutcome:
    """Everything :class:`TurnCoordinator` needs to complete a (non-escalating) turn."""

    response: str
    awaiting_approval: bool
    awaiting_input: bool
    pending_approval: Optional[Dict[str, Any]]
    stop_reason: str
    ls_outputs: Dict[str, Any]


class TurnResponsePostProcessor:
    """Turns a raw LangGraph ``final_state`` into a completed turn's response
    text, pause flags, and metrics.

    Pulled out of ``TurnCoordinator`` so the marker-stripping / plan-extraction
    / stop-reason / duration-and-speed logic that used to be inlined in the
    invoke try-block has one home, separate from event publication (which stays
    on the coordinator, since it owns ``event_bus``).
    """

    @staticmethod
    def check_voice_escalate(
        final_state: Dict[str, Any], category: str
    ) -> Tuple[bool, Optional[str]]:
        """True when voice OR contact front desk asked to escalate to full agent."""
        if category in ("voice", "contact") and final_state.get("voice_escalate"):
            return True, final_state.get("voice_escalate_reason", "")
        return False, None

    @staticmethod
    def extract_front_desk_ack(final_state: Dict[str, Any]) -> str:
        """Best-effort user-facing text from a contact/voice turn before handoff."""
        from langchain_core.messages import AIMessage

        for msg in reversed(final_state.get("messages") or []):
            if isinstance(msg, AIMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
                content = strip_orchestration_markers(content or "").strip()
                if content:
                    return content
                break
        fallback = (final_state.get("final_response") or "").strip()
        return fallback or "On it — working on that."

    @staticmethod
    def _generation_error_detail(final_state: Dict[str, Any]) -> Optional[str]:
        """Raw provider error text (e.g. LM Studio's "Context size has been exceeded"),
        preserved by lc_wrapper.py in additional_kwargs.error_detail. None if the last
        AI message didn't fail."""
        from langchain_core.messages import AIMessage

        messages = final_state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                if getattr(msg, "tool_calls", None):
                    continue
                if msg.additional_kwargs.get("finish_reason") == "error":
                    return msg.additional_kwargs.get("error_detail") or "(no detail captured)"
                break
        return None

    @staticmethod
    def _generation_error_message(final_state: Dict[str, Any]) -> Optional[str]:
        """If the last AI message's generation actually failed (adapter set
        finish_reason="error" -- e.g. context-window overflow), return an honest
        fallback instead of letting an empty final_response silently become
        "Task completed." with no trace of the underlying failure."""
        detail = TurnResponsePostProcessor._generation_error_detail(final_state)
        if detail is None:
            return None
        return ("I hit an error generating a response (possibly a context-length "
                "limit or a model/provider failure) and couldn't complete this turn. "
                "Please try again or rephrase.")

    @staticmethod
    def process(
        final_state: Dict[str, Any],
        *,
        metrics: Dict[str, Any],
        turn_start_time: float,
    ) -> TurnOutcome:
        generation_error = TurnResponsePostProcessor._generation_error_message(final_state)
        # Do not default to "Task completed." yet — a plan-approval pause must win over that
        # fallback, and an empty response is resolved after interrupt detection below.
        final_response = final_state.get("final_response") or generation_error or ""
        final_response = strip_orchestration_markers(final_response)

        # Strip [[PLAN]] markers from final_response so they never reach the UI.
        # The planner already published ExecutionPlanEvent live; do not re-emit here.
        extracted_plan, clean_text = extract_plan_from_text(final_response)
        final_response = clean_text
        if extracted_plan:
            logger.debug(
                "[ TURN ] Plan markers stripped from final_response "
                "(live ExecutionPlanEvent already published by planner)."
            )

        is_error = bool(generation_error or str(final_response).startswith("⚠️"))
        awaiting_approval = bool(final_state.get("graph_interrupt_pending")) and not is_error
        pending_approval = final_state.get("pending_approval")
        awaiting_input = bool(final_state.get("paused_for_input") or pending_approval) and not is_error
        if awaiting_approval or awaiting_input:
            logger.debug(
                f"[ TURN ] Turn ended paused: awaiting_approval={awaiting_approval} "
                f"awaiting_input={awaiting_input} pending_approval={bool(pending_approval)}"
            )

        if pending_approval:
            final_response = (
                pending_approval.get("title")
                or pending_approval.get("prompt")
                or final_response
                or ""
            )

        if not final_response.strip():
            if awaiting_approval:
                final_response = (
                    "Here's the proposed execution plan. "
                    "Review the steps above and tell me when to proceed."
                )
            elif final_state.get("plan_replanned") or extracted_plan:
                final_response = (
                    "A step hit a snag, so I revised the plan and continued execution."
                )
            elif generation_error:
                final_response = generation_error
            else:
                final_response = "Task completed."
        elif awaiting_approval and final_response.strip().lower() in {
            "task completed.",
            "task completed",
        }:
            # Planner JSON-only turns used to hit the early "Task completed." default
            # before interrupt detection ran — never show that under a pending plan.
            final_response = (
                "Here's the proposed execution plan. "
                "Review the steps above and tell me when to proceed."
            )

        duration = time.time() - turn_start_time
        metrics["duration"] = round(duration, 2)
        completion_tokens = metrics.get("completion_tokens", 0)
        if duration > 0 and completion_tokens > 0:
            metrics["speed"] = round(completion_tokens / duration, 2)
        else:
            metrics["speed"] = 0.0

        stop_reason = turn_metrics.classify_stop_reason(final_response, final_state)
        metrics["stop_reason"] = stop_reason
        if stop_reason == "error":
            # last_turn_metrics is the same dict object UIStateSyncer merges into the
            # outgoing ai_response payload (ui_state_syncer.py: "if turn_metrics:
            # payload.update(turn_metrics)") -- setting it here is enough for real
            # clients AND the eval harness (evals/live.py) to see the actual provider
            # error, not just a bare "error" stop_reason.
            metrics["error_detail"] = TurnResponsePostProcessor._generation_error_detail(final_state)

        ls_outputs = {
            "response": final_response,
            "stop_reason": stop_reason,
            "tool_calls": metrics.get("total_tool_calls", 0),
            "duration_s": metrics.get("duration", 0),
        }
        if stop_reason == "error":
            # Surface the actual provider error (e.g. LM Studio's "Context size has been
            # exceeded") on the event payload itself -- previously only visible by grepping
            # the Brain's own log file, invisible to both real clients and the eval harness.
            ls_outputs["error_detail"] = TurnResponsePostProcessor._generation_error_detail(final_state)

        return TurnOutcome(
            response=final_response,
            awaiting_approval=awaiting_approval,
            awaiting_input=awaiting_input,
            pending_approval=pending_approval,
            stop_reason=stop_reason,
            ls_outputs=ls_outputs,
        )
