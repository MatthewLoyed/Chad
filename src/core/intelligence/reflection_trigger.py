"""
reflection_trigger.py — off-hot-path reflection triggers (issue #236 Step 4).

Subscribes to TurnFeedbackEvent and runs a dry-run reflection pass on thumbs-down.
Writes proposals to evals/runs/reflect_proposal_latest.md — never auto-writes to persona
or the knowledge graph (human must run reflection --write).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Optional

logger = logging.getLogger("ReflectionTrigger")

_reflection_task: Optional[asyncio.Task] = None


def _judge_model() -> str:
    """Resolve the judge model: prefers reflection.model -> active supervisor model."""
    from src.shared.config_manager import ConfigManager
    return (
        os.getenv("CHAD_REFLECTION_JUDGE_MODEL")
        or ConfigManager.get("reflection.model")
        or ConfigManager.get_active_model()
    )


def _convos() -> int:
    try:
        return max(1, int(os.getenv("CHAD_REFLECTION_CONVOS", "8")))
    except ValueError:
        return 8


def _is_turn_reflection_candidate(supabase) -> bool:
    """Fast Tier-1 check: did a tool fail recently, or does the latest user turn contain correction cues?"""
    try:
        # 1. Check for any tool failure in the last 120 seconds
        recent_fails = supabase.query(
            "tool_outcome_log",
            select="outcome,created_at",
            filters={"outcome": "eq.failure"},
            order="created_at.desc",
            limit=1,
        )
        if recent_fails:
            fail_time_str = recent_fails[0].get("created_at")
            if fail_time_str:
                try:
                    fail_time = datetime.fromisoformat(fail_time_str.replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - fail_time).total_seconds() < 120:
                        logger.info("[ REFLECT ] Detected recent tool failure — qualifying turn for reflection")
                        return True
                except Exception:
                    pass

        # 2. Check if the latest user message contains contrast/correction signals
        recent_msgs = supabase.query(
            "messages",
            select="role,content,created_at",
            order="created_at.desc",
            limit=2,
        )
        if recent_msgs:
            user_msg = next((m for m in recent_msgs if m.get("role") == "user"), None)
            if user_msg:
                content = str(user_msg.get("content") or "").lower()
                correction_cues = (
                    "instead", "actually", "wrong", "meant", "no,", "no ", "not ",
                    "don't", "do not", "should be", "should have", "is at", "url is",
                    "stop doing", "never do", "didn't work", "did not work",
                )
                if any(cue in content for cue in correction_cues):
                    logger.info("[ REFLECT ] Detected user correction cues in prompt — qualifying turn for reflection")
                    return True
    except Exception as e:
        logger.debug(f"[ REFLECT ] Fast filter check failed: {e}")
    return False


async def _run_negative_feedback_reflection(turn_id: str) -> None:
    from src.core.intelligence.reflection import propose_lessons, _render_proposal
    from pathlib import Path

    model = _judge_model()
    convos = _convos()
    logger.info(
        "[ REFLECT ] Thumbs-down on turn %s — dry-run reflection (%d convos, model=%s)",
        turn_id,
        convos,
        model,
    )
    try:
        proposal = await propose_lessons(convos, model)
        report = _render_proposal(proposal)
        runs_dir = Path(__file__).resolve().parents[3] / "evals" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        out = runs_dir / "reflect_proposal_latest.md"
        out.write_text(report, encoding="utf-8")
        logger.info(
            "[ REFLECT ] Proposal saved (%d admitted, %d questions): %s",
            len(proposal.admitted),
            len(proposal.questions),
            out,
        )
    except Exception as e:
        logger.warning("[ REFLECT ] Thumbs-down reflection failed: %s", e, exc_info=True)


async def _run_instant_failure_reflection(turn_id: Optional[str] = None) -> None:
    """Propose lessons for a failed/corrected turn; human review owns application."""
    from src.core.intelligence.reflection import propose_lessons
    from src.core.intelligence.reflection_lessons_store import persist_lesson_and_publish
    from src.infrastructure.database.supabase_manager import get_supabase_manager
    from src.shared.config_manager import ConfigManager

    if not ConfigManager.get("reflection.propose_on_failure", False):
        return

    supabase = get_supabase_manager()
    if not supabase or not getattr(supabase, "enabled", True):
        return

    if not _is_turn_reflection_candidate(supabase):
        return

    model = _judge_model()
    floor = float(ConfigManager.get("reflection.failure_reflection_confidence_floor", 0.75))
    logger.info("[ REFLECT ] Running instant failure reflection on turn %s (model=%s, floor=%.2f)", turn_id, model, floor)

    try:
        proposal = await propose_lessons(convos=1, model=model, confidence_floor=floor, max_per_cycle=2)
        if not proposal.admitted:
            logger.info("[ REFLECT ] Instant reflection completed — no new lessons admitted.")
            return

        for lesson in proposal.admitted:
            scope = lesson.get("scope") or "tools"
            logger.info(
                "[ REFLECT ] Lesson proposed for review (scope=%s, conf=%.2f)",
                scope, float(lesson.get("confidence", 0.0) or 0.0)
            )
            await persist_lesson_and_publish(supabase, lesson, "lesson", auto_apply=False)
    except Exception as e:
        logger.warning("[ REFLECT ] Instant failure reflection failed: %s", e, exc_info=True)


def _schedule_feedback_reflection(turn_id: str) -> None:
    global _reflection_task
    if _reflection_task and not _reflection_task.done():
        logger.debug("[ REFLECT ] Reflection already in flight — skipping duplicate enqueue")
        return
    _reflection_task = asyncio.create_task(_run_negative_feedback_reflection(turn_id))


def _schedule_turn_reflection(turn_id: Optional[str] = None) -> None:
    global _reflection_task
    if _reflection_task and not _reflection_task.done():
        logger.debug("[ REFLECT ] Reflection already in flight — skipping duplicate enqueue")
        return
    _reflection_task = asyncio.create_task(_run_instant_failure_reflection(turn_id))


def _on_feedback(event) -> None:
    if getattr(event, "rating", None) != "negative":
        return
    _schedule_feedback_reflection(event.turn_id)


def _on_turn_complete(event) -> None:
    _schedule_turn_reflection(event.turn_id)


def register_reflection_triggers(event_bus) -> None:
    """Wire review-only feedback/failure reflection. Personal memory uses approved tools."""
    if getattr(event_bus, "_reflection_triggers_registered", False):
        return

    from src.application.events import TurnFeedbackEvent, TurnCompleteEvent

    event_bus.subscribe(TurnFeedbackEvent, _on_feedback)
    event_bus.subscribe(TurnCompleteEvent, _on_turn_complete)
    event_bus._reflection_triggers_registered = True  # type: ignore[attr-defined]
    logger.info("[ REFLECT ] Registered review-only feedback and failure triggers")
