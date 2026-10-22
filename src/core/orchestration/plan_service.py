"""Single publish path for execution plan UI state."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PlanService")

# Follow-ups the GUI/evals send after a plan-approval pause. These MUST NOT become
# plan_goal / ExecutionPlanEvent.goal — that made goal="proceed" and re-scout thrash
# (HITL 2026-07-24 din_plan_wedding_site).
_PLAN_FOLLOWUP_EXACT = frozenset({
    "proceed", "ok", "okay", "yes", "yep", "sure", "go", "go ahead", "lgtm", "ship it",
})
_PLAN_FOLLOWUP_PHRASES = (
    "approved",
    "looks good",
    "execute the plan",
    "execute the existing plan",
    "continue executing the plan",
    "keep going",
    "do not create a new plan",
    "call complete_step",
)


def is_plan_followup_message(text: Optional[str]) -> bool:
    """True when ``text`` is an approval/continue nudge, not the user's original ask."""
    t = (text or "").strip().lower()
    if not t:
        return True
    if t in _PLAN_FOLLOWUP_EXACT:
        return True
    # Short replies that are just an approval word/phrase.
    if len(t) <= 96 and any(p in t for p in _PLAN_FOLLOWUP_PHRASES):
        return True
    # Eval / GUI approval & continue nudges (can be longer than a chat ack).
    if "continue executing" in t and "plan" in t:
        return True
    if "execute the existing plan" in t and "do not create a new plan" in t:
        return True
    if "emit complete_step" in t or "emit `complete_step`" in t:
        return True
    if t.startswith("approved") and "plan" in t:
        return True
    if re.match(r"^(please\s+)?(proceed|continue)\b", t) and len(t) <= 160:
        return True
    return False


class PlanService:
    """Central service for plan snapshot emission — all graph nodes use this."""

    @staticmethod
    def resolve_turn_id(state: Dict[str, Any]) -> Optional[str]:
        return state.get("turn_id") or state.get("session_id")

    @staticmethod
    def resolve_goal(state: Dict[str, Any], goal: Optional[str] = None) -> Optional[str]:
        """North-star goal for the plan card / supervisor.

        Prefer an explicit ``goal``, then persisted ``plan_goal``, then the latest
        non-followup HumanMessage. Never latch onto bare ``proceed`` / continue nudges.
        """
        if goal and not is_plan_followup_message(goal):
            return goal
        existing = state.get("plan_goal")
        if existing and not is_plan_followup_message(str(existing)):
            return str(existing)
        try:
            from langchain_core.messages import HumanMessage

            for msg in reversed(state.get("messages", []) or []):
                if isinstance(msg, HumanMessage):
                    content = str(msg.content or "").strip()
                    if content and not is_plan_followup_message(content):
                        return content
        except Exception:
            pass
        # Last resort: keep a non-followup existing goal even if somehow flagged, else None.
        if existing:
            return str(existing)
        return goal

    @classmethod
    def emit_snapshot(
        cls,
        state: Dict[str, Any],
        plan: List[Dict[str, Any]],
        *,
        goal: Optional[str] = None,
        replace: bool = True,
        gather_brief: Optional[str] = None,
    ) -> None:
        """Publish a plan snapshot to the EventBus (idempotent upsert on the client)."""
        turn_id = cls.resolve_turn_id(state)
        if not turn_id:
            logger.warning("[ PlanService ] Skipping plan snapshot: no turn_id or session_id in state.")
            return

        resolved_goal = cls.resolve_goal(state, goal)
        try:
            from src.application.events.event_bus import EventBus
            from src.application.events.events import ExecutionPlanEvent

            EventBus.get_instance().publish(
                ExecutionPlanEvent(
                    plan=plan,
                    plan_id=turn_id,
                    goal=resolved_goal,
                    turn_id=turn_id,
                    replace=replace,
                    gather_brief=gather_brief,
                )
            )
            logger.info("[ PlanService ] Published plan snapshot (%d steps, turn_id=%s).", len(plan), turn_id)
        except Exception as exc:
            logger.warning("[ PlanService ] Failed to publish plan snapshot: %s", exc)
