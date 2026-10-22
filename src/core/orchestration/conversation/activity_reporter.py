"""
AutonomousActivityReporter — Non-USER Turn Observability

Extracted from ConversationOrchestrator to satisfy the single-responsibility
principle.  Owns two concerns that only apply when an autonomous agent
(cron, webhook, skill, followup) completes a turn with tool activity:

1. **Durable run log** — writes to ``src.core.monitoring.autonomous_run_log``
   so the Automations UI can display a history of what the agent did.

2. **Dashboard feed entry** — appends a ``status`` entry to the shared
   ``dashboard_feed`` so the Dashboard workspace shows a real-time activity
   blip without requiring the model to call ``update_dashboard`` itself.

Both writes are best-effort; failures are logged and never propagate to the
caller (the orchestrator's ``finalize_turn`` fires them via
``_fire_and_forget``).
"""

import logging
from typing import List, Optional

from src.shared.logger import setup_logger

logger = setup_logger("AutonomousActivityReporter")

# Sentinel for the TurnInitiator.USER value — imported lazily to avoid a
# circular import at module load time.
_USER_INITIATOR_VALUE = "user"


class AutonomousActivityReporter:
    """Records autonomous-turn activity to the run log and dashboard feed.

    Designed to be used via ``_fire_and_forget`` so the orchestrator's
    ``finalize_turn`` is never blocked by I/O.
    """

    # ------------------------------------------------------------------
    # Durable run log
    # ------------------------------------------------------------------

    async def record_run(
        self,
        *,
        initiator: str,
        category: str,
        user_input: Optional[str],
        model_response: Optional[str],
        tools_executed: List[str],
        duration_sec: float,
    ) -> None:
        """Write a durable entry to the autonomous run log.

        This is a no-op for USER-initiated turns; the run log is intended
        only for scheduled/autonomous activity.

        Args:
            initiator:       ``TurnInitiator.value`` string (e.g. ``"system"``).
            category:        Turn category (e.g. ``"agent"``, ``"chat"``).
            user_input:      Prompt text (may be None for cron turns).
            model_response:  Final model answer.
            tools_executed:  Tool names called during the turn.
            duration_sec:    Wall-clock duration of the turn.
        """
        if initiator == _USER_INITIATOR_VALUE:
            return  # USER turns are not recorded here

        try:
            from src.core.monitoring.autonomous_run_log import record_llm_turn

            record_llm_turn(
                initiator=initiator,
                category=category,
                user_input=user_input,
                model_response=model_response,
                tools_executed=tools_executed,
                duration_sec=duration_sec,
            )
        except Exception as exc:
            logger.debug(f"[ ACTIVITY ] Autonomous run log failed (non-fatal): {exc}")

    # ------------------------------------------------------------------
    # Dashboard feed
    # ------------------------------------------------------------------

    async def surface_to_dashboard(
        self,
        *,
        initiator: str,
        category: str,
        session_key: Optional[str],
        tools_executed: List[str],
        model_response: Optional[str],
    ) -> None:
        """Append a dashboard status entry for an autonomous turn that used tools.

        Reuses the existing ``dashboard_entries`` feed (the same one
        ``update_dashboard`` writes to) rather than inventing a separate
        activity log surface.  Pure no-ops (``HEARTBEAT_OK``, idle cron
        checks) are excluded by the ``tools_executed`` guard.

        Args:
            initiator:       ``TurnInitiator.value`` string.
            category:        Turn category.
            session_key:     Session key for the turn (used in metadata).
            tools_executed:  Tool names called during the turn.
            model_response:  Final model answer (truncated to 500 chars in feed).
        """
        if initiator == _USER_INITIATOR_VALUE or not tools_executed:
            return  # USER turns and no-ops are not surfaced

        try:
            from src.core.intelligence import dashboard_feed

            _label_map = {
                "system": "Scheduled automation",
                "webhook": "Webhook",
                "skill": "Skill",
                "followup": "Follow-up",
            }
            source_label = _label_map.get(initiator, initiator.title())
            tools_summary = ", ".join(tools_executed)
            body = (model_response or "").strip()[:500]

            await dashboard_feed.append_entry(
                kind="status",
                title=f"{source_label} took action: {tools_summary}",
                body=body,
                metadata={
                    "initiator": initiator,
                    "category": category,
                    "tools_executed": tools_executed,
                    "session_key": session_key,
                },
            )
        except Exception as exc:
            logger.error(
                f"[ ACTIVITY ] Failed to surface autonomous action on dashboard: {exc}",
                exc_info=True,
            )
