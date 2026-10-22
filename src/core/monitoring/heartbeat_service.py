"""
Heartbeat Service - Autonomous Periodic Awareness

Runs on Brain server (24/7), waking up every 30 minutes to check
for important events and send Telegram notifications if needed.

Based on OpenClaw's heartbeat pattern: batched monitoring in single turn.
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from src.shared.logger import setup_logger

if TYPE_CHECKING:
    from src.core.orchestration.conversation_orchestrator import ConversationOrchestrator

class HeartbeatService:
    """
    Autonomous heartbeat loop that checks for important events.

    Monitors checklist defined in HEARTBEAT.md and sends notifications
    via Telegram when important events detected.
    """

    def __init__(self, orchestrator: "ConversationOrchestrator"):
        self._logger = setup_logger("HeartbeatService")
        self._orchestrator = orchestrator
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        # Propose-only reflection runs on its own slow wall-clock cadence (see config
        # `reflection.*`). Seed the timer in the past so the first cycle fires shortly
        # after boot rather than waiting a full interval.
        self._last_reflection_ts: float = 0.0
        # Sleep cognify (#611) is also propose-only on its own cadence, gated further
        # by an overnight local-hour window (sleep_cognify.night_hours_only).
        self._last_sleep_cognify_ts: float = 0.0
        self._last_scavenge_ts: float = time.time()
        self._last_check: Optional[datetime] = None

        from src.core.autonomy.curiosity_service import CuriosityService
        from src.core.autonomy.impulse_engine import ImpulseEngine
        self._curiosity_service = CuriosityService()
        self._impulse_engine = ImpulseEngine()

    async def start(self, interval_minutes: Optional[int] = None):
        """
        Start the heartbeat loop.

        Args:
            interval_minutes: How often to check (default: from config or 30 minutes)
        """
        if self._running:
            self._logger.warning("Heartbeat already running")
            return

        if interval_minutes is None:
            from src.shared.config_manager import ConfigManager
            interval_minutes = int(ConfigManager.get("daemons.heartbeat_interval_minutes", 30) or 30)

        self._running = True
        self._interval_seconds = interval_minutes * 60
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._logger.info(f"Heartbeat service started (interval: {interval_minutes} minutes)")

    async def stop(self):
        """Stop the heartbeat loop."""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        self._logger.info("Heartbeat service stopped")

    def get_status(self) -> dict:
        """Get current heartbeat status.

        "online" means the loop is alive AND the live daemons.heartbeat/daemons.enabled
        flags currently allow it to fire; "paused" means the asyncio task is still
        alive (so no restart is needed to re-enable it) but the GUI toggle has it
        switched off, so no heartbeat cycles are executing.
        """
        from src.shared.config_manager import ConfigManager
        from src.core.monitoring.daemon_status import daemon_enabled
        interval = int(ConfigManager.get("daemons.heartbeat_interval_minutes", 30) or 30)
        if not self._running:
            status = "offline"
        elif daemon_enabled("heartbeat"):
            status = "online"
        else:
            status = "paused"
        return {
            "status": status,
            "interval_minutes": interval,
            "last_check": self._last_check.isoformat() if self._last_check else None
        }

    async def _heartbeat_loop(self):
        """Main heartbeat loop - wakes up periodically to check things."""
        counter = 0
        while self._running:
            try:
                # High-frequency liveness log (1 min) for event loop diagnostics
                if counter % 12 == 0:  # Every 12 iterations (approx 1 min)
                    self._logger.debug(f"[ LIVENESS ] Event loop is ALIVE (iteration {counter})")
                
                await asyncio.sleep(5)
                counter += 1

                # Re-read interval each tick so Automations UI changes apply without restart.
                from src.shared.config_manager import ConfigManager
                self._interval_seconds = int(ConfigManager.get("daemons.heartbeat_interval_minutes", 30) or 30) * 60
                tick_mod = max(1, self._interval_seconds // 5)

                # Re-read the enabled flag (daemons.heartbeat AND the master daemons.enabled
                # switch / CHAD_DAEMONS_ENABLED env override) each tick too, same live-reload
                # pattern as the interval above. Lets the Settings -> Automations GUI toggle
                # actually pause a live heartbeat loop without a process restart (#473) — the
                # asyncio task keeps sleeping/ticking so re-enabling is instant, but no
                # heartbeat/reflection work runs while disabled.
                from src.core.monitoring.daemon_status import daemon_enabled
                if not daemon_enabled("heartbeat"):
                    continue

                # Original periodic task (every N mins)
                if counter % tick_mod == 0:
                    await self._execute_heartbeat()

                # Propose-only reflection on its own slower cadence (default 24h).
                await self._maybe_run_reflection()

                # Overnight sleep-cognify pass on its own cadence + night-hours window.
                await self._maybe_run_sleep_cognify()

                # Autonomous discovery scavenger pass (runs if stash is low or on 12h cadence).
                await self._maybe_run_scavenger()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Heartbeat loop error: {e}", exc_info=True)

    async def _execute_heartbeat(self):
        """
        Execute one heartbeat cycle.

        Reads heartbeat checklist from Supabase (persona_config)
        and triggers a SYSTEM turn to check all items in batched fashion.
        """
        if self._impulse_engine.is_quiet_hours():
            self._logger.info("[ HEARTBEAT ] Quiet hours active (23:30 - 08:30) — staying silent.")
            return

        self._logger.info("Executing heartbeat check (Cloud)...")
        self._last_check = datetime.now()

        try:
            # 1. Fetch checklist from PromptRegistry (local files win per #589), falling back to Supabase
            checklist = ""
            try:
                from src.infrastructure.repositories.prompt_registry import PromptRegistry
                registry = PromptRegistry.get_instance()
                if getattr(registry, "_loaded", False):
                    checklist = registry.get_prompt("heartbeat")
            except Exception as e:
                self._logger.debug(f"PromptRegistry lookup failed for heartbeat: {e}")

            if not checklist or not checklist.strip():
                from src.infrastructure.database.supabase_manager import get_supabase_manager
                supabase = get_supabase_manager()
                res = supabase.query("persona_config", filters={"id": "eq.heartbeat"}, limit=1)
                if not res or "content" not in res[0]:
                    self._logger.warning("Heartbeat checklist not found in Supabase persona_config. Using default.")
                    checklist = self._get_default_checklist_text()
                else:
                    checklist = res[0]["content"]

            # 2. Get fallback session info (OpenClaw pattern)
            from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository
            chat_repo = TelegramChatRepository()
            default_chat_id = chat_repo.get_default_chat_id()
            
            # RequestContext.session_id is a required str (no default) — fall back to a
            # generic autonomous session key (same pattern as gmail_handler.py) rather than
            # None, which would raise a pydantic ValidationError below and silently no-op
            # every heartbeat cycle whenever no default Telegram chat is configured yet.
            session_key = "autonomous:heartbeat"
            if default_chat_id:
                chat = chat_repo.get_chat(default_chat_id)
                if chat:
                    session_key = chat.session_key
                    self._logger.info(f"Using fallback session {session_key} for heartbeat")

            # 3. Dynamic Tool Schema Injection (Self-Healing)
            # Detect tool names in backticks: `tool_name`
            import re
            import json
            tool_names = re.findall(r'`(\w+)`', checklist)
            tool_schemas_text = ""
            
            if tool_names:
                from src.infrastructure.tools import ToolRegistry
                registry = ToolRegistry()
                for tname in set(tool_names):
                    tool = registry.get_tool(tname)
                    if tool:
                        try:
                            # Use schema if available
                            schema = tool.get_schema() if hasattr(tool, 'get_schema') else {"name": tname, "description": "No detailed schema available"}
                            tool_schemas_text += f"\n### Tool: {tname}\n```json\n{json.dumps(schema, indent=2)}\n```\n"
                        except Exception as e:
                            self._logger.warning(f"Failed to fetch schema for heartbeat tool {tname}: {e}")

            # 4. Execute turn via ConversationOrchestrator's full ReAct loop
            from src.core.orchestration.conversation_orchestrator import TurnInitiator
            from src.shared.context import RequestContext
            ctx = RequestContext(platform="system", session_id=session_key)
            
            try:
                from src.infrastructure.repositories.prompt_registry import PromptRegistry
                template = PromptRegistry.get_instance().get_prompt("event_heartbeat")
                full_prompt = template.replace("{checklist}", checklist)
            except Exception:
                full_prompt = (
                    "BACKGROUND HEARTBEAT CYCLE\n"
                    "You are running in autonomous mode. Process the following checks and notify the user via Telegram ONLY if important events are detected.\n\n"
                    f"## Checklist\n{checklist}\n"
                )
            if tool_schemas_text:
                full_prompt += f"\n## Tool Definitions (Manuals)\n{tool_schemas_text}\n"

            # Inject Curiosity Stash awareness so model knows verified discoveries are available
            stash_status = self._curiosity_service.get_status()
            avail_count = stash_status.get("available_count", 0)
            if avail_count > 0:
                full_prompt += (
                    f"\n## Curiosity Stash\n"
                    f"There are {avail_count} verified un-sent discovery items in your stash. "
                    f"Use `manage_curiosity` with action='get_next' to retrieve a verified link "
                    f"if you decide to drop a spontaneous find. Remember: ZERO forced metaphors!\n"
                )

            response_text = await self._orchestrator.process_request(
                prompt=full_prompt,
                context=ctx,
                initiator=TurnInitiator.SYSTEM,
                category='autonomous'
            )

            self._logger.info(f"Heartbeat execution finished. Response: {response_text[:50]}...")

            # If response is non-silent and was sent out, record that spontaneous outreach occurred
            if response_text and response_text.strip().upper() != "HEARTBEAT_OK":
                self._impulse_engine.record_spontaneous_sent()

        except Exception as e:
            self._logger.error(f"Heartbeat execution failed: {e}", exc_info=True)

    async def _maybe_run_reflection(self):
        """Run the PROPOSE-ONLY reflection cycle if its interval has elapsed.

        Reads recent history, proposes admissible lessons, persists the report, surfaces
        each lesson/question as its own row in `reflection_lessons` for GUI review/approve
        (#194), and notifies via Telegram. This cycle itself never calls apply_admitted()
        for the whole batch — admitting a lesson stays per-item, either a human approving
        it in the GUI/CLI, or (opt-in) `reflection.auto_write_confidence_floor` applying it
        immediately above a confidence floor. Fully best-effort; never breaks the heartbeat.
        """
        try:
            from src.shared.config_manager import ConfigManager
            from src.core.monitoring.daemon_status import daemons_master_enabled
            if not daemons_master_enabled() or not ConfigManager.get("reflection.enabled", False):
                return

            interval_hours = float(ConfigManager.get("reflection.interval_hours", 24) or 24)
            now = time.time()
            if (now - self._last_reflection_ts) < interval_hours * 3600:
                return
            self._last_reflection_ts = now  # mark before running so a slow run can't double-fire

            model = ConfigManager.get("reflection.model", "google/gemma-4-26b-a4b-qat")
            convos = int(ConfigManager.get("reflection.convos", 15) or 15)
            floor = float(ConfigManager.get("reflection.confidence_floor", 0.7) or 0.7)
            auto_write_floor = ConfigManager.get("reflection.auto_write_confidence_floor", None)
            auto_write_floor = float(auto_write_floor) if auto_write_floor is not None else None

            self._logger.info(f"[ REFLECTION ] Running propose-only cycle (model={model}, convos={convos})...")
            from src.core.intelligence.reflection import propose_lessons, _render_proposal

            proposal = await propose_lessons(convos, model, confidence_floor=floor)

            # Persist the human-reviewable report (same path the CLI writes).
            try:
                from pathlib import Path
                runs_dir = Path("evals") / "runs"
                runs_dir.mkdir(parents=True, exist_ok=True)
                (runs_dir / "reflect_proposal_latest.md").write_text(
                    _render_proposal(proposal), encoding="utf-8")
            except Exception as e:
                self._logger.warning(f"[ REFLECTION ] Failed to persist report: {e}")

            # Surface each admitted lesson / open question as its own reviewable row
            # (GUI review/approve queue, #194) — this is what "NOTHING written" below
            # refers to: apply_admitted() for the WHOLE batch is still never called here.
            # auto_write_confidence_floor opts a lesson INTO being written immediately;
            # it stays visible in the GUI as already-applied, never silently invisible.
            try:
                from src.infrastructure.database.supabase_manager import get_supabase_manager
                from src.core.intelligence.reflection_lessons_store import persist_lesson_and_publish
                supabase = get_supabase_manager()
                if supabase and supabase.enabled:
                    for lesson in proposal.admitted:
                        auto = auto_write_floor is not None and float(lesson.get("confidence", 0.0) or 0.0) >= auto_write_floor
                        await persist_lesson_and_publish(supabase, lesson, "lesson", auto_apply=auto)
                    for q in proposal.questions:
                        await persist_lesson_and_publish(supabase, q, "question")
            except Exception as e:
                self._logger.warning(f"[ REFLECTION ] Failed to persist review queue rows: {e}")

            n_admitted = len(proposal.admitted)
            n_questions = len(proposal.questions)
            self._logger.warning(
                f"[ REFLECTION ] Propose-only cycle done: {n_admitted} lesson(s) to review, "
                f"{n_questions} question(s). NOTHING written by this cycle itself "
                f"(individual lessons may have been auto-applied per auto_write_confidence_floor).")

            try:
                from src.core.monitoring.autonomous_run_log import SOURCE_REFLECTION, record_run
                record_run(
                    SOURCE_REFLECTION,
                    outcome="ok",
                    summary=f"{n_admitted} lesson(s) proposed, {n_questions} question(s).",
                    metadata={"admitted": n_admitted, "questions": n_questions},
                    silent=(n_admitted == 0 and n_questions == 0),
                )
            except Exception:
                pass

            if (n_admitted or n_questions) and ConfigManager.get("reflection.notify_telegram", True):
                self._notify_reflection(proposal)
        except Exception as e:
            self._logger.error(f"[ REFLECTION ] Propose-only cycle failed: {e}", exc_info=True)

    async def _maybe_run_sleep_cognify(self):
        """Run the offline sleep-cognify pass (#611) if due — its own interval AND
        (by default) an overnight local-hour window, since the whole point is to run
        while the user is asleep rather than mid-conversation.

        Writes the non-sensitive auto-admit batch (typed edges / observations)
        directly to the knowledge graph. Tier-1 sensitive items and any core-profile
        rewrite are NEVER auto-applied here — they only ever reach the graph via the
        review queue (reflection_lessons, kind='sleep_cognify'), same GUI panel as
        reflective lessons (#194).
        """
        try:
            from src.shared.config_manager import ConfigManager
            from src.core.monitoring.daemon_status import daemons_master_enabled
            if not daemons_master_enabled() or not ConfigManager.get("sleep_cognify.enabled", False):
                return

            interval_hours = float(ConfigManager.get("sleep_cognify.interval_hours", 24) or 24)
            now = time.time()
            if (now - self._last_sleep_cognify_ts) < interval_hours * 3600:
                return

            if ConfigManager.get("sleep_cognify.night_hours_only", True):
                start_hour = int(ConfigManager.get("sleep_cognify.night_start_hour", 1) or 1)
                end_hour = int(ConfigManager.get("sleep_cognify.night_end_hour", 5) or 5)
                current_hour = datetime.now().hour
                in_window = (
                    (start_hour <= current_hour < end_hour) if start_hour < end_hour
                    else (current_hour >= start_hour or current_hour < end_hour)
                )
                if not in_window:
                    return  # interval elapsed but outside the overnight window — retry next tick

            self._last_sleep_cognify_ts = now  # mark before running so a slow run can't double-fire

            model = ConfigManager.get("sleep_cognify.model", "google/gemma-4-26b-a4b-qat")
            floor = float(ConfigManager.get("sleep_cognify.confidence_floor", 0.6) or 0.6)
            max_chars = int(ConfigManager.get("sleep_cognify.core_profile_max_chars", 800) or 800)

            self._logger.info(f"[ SLEEP_COGNIFY ] Running overnight cognify cycle (model={model})...")
            from src.core.intelligence.sleep_cognify import (
                propose_cognify, apply_admitted, persist_pending_items, _render_proposal,
            )

            proposal = await propose_cognify(
                model, confidence_floor=floor, core_profile_max_chars=max_chars)

            try:
                from pathlib import Path
                runs_dir = Path("evals") / "runs"
                runs_dir.mkdir(parents=True, exist_ok=True)
                (runs_dir / "sleep_cognify_proposal_latest.md").write_text(
                    _render_proposal(proposal), encoding="utf-8")
            except Exception as e:
                self._logger.warning(f"[ SLEEP_COGNIFY ] Failed to persist report: {e}")

            write_results = await apply_admitted(proposal)
            n_queued = await persist_pending_items(proposal)
            n_written = sum(1 for ok in write_results.values() if ok)

            self._logger.warning(
                f"[ SLEEP_COGNIFY ] Cycle done: {n_written}/{len(write_results)} auto-admit "
                f"item(s) written to the graph, {n_queued} Tier-1/core-profile item(s) "
                f"queued for review.")

            try:
                from src.core.monitoring.autonomous_run_log import SOURCE_SLEEP_COGNIFY, record_run
                record_run(
                    SOURCE_SLEEP_COGNIFY,
                    outcome="ok",
                    summary=f"{n_written} auto-written, {n_queued} queued for review.",
                    metadata={"written": n_written, "queued": n_queued},
                    silent=(n_written == 0 and n_queued == 0),
                )
            except Exception:
                pass
        except Exception as e:
            self._logger.error(f"[ SLEEP_COGNIFY ] Cycle failed: {e}", exc_info=True)

    async def _maybe_run_scavenger(self):
        """Run the autonomous curiosity scavenger if stash is low or interval elapsed."""
        try:
            from src.core.monitoring.daemon_status import daemon_enabled
            if not daemon_enabled("scavenger", default=True):
                return

            now = time.time()
            avail = len(self._curiosity_service.get_available_items())
            # Run if stash has fewer than 2 items, or if >12h since last scavenge
            if avail >= 2 and (now - self._last_scavenge_ts) < 43200:
                return

            self._last_scavenge_ts = now
            self._logger.info(f"[ SCAVENGER ] Running discovery scavenge (current stash items: {avail})...")
            added = await self._curiosity_service.scavenge()
            self._logger.info(f"[ SCAVENGER ] Discovery scavenge complete. {added} item(s) added.")
        except Exception as e:
            self._logger.warning(f"[ SCAVENGER ] Scavenger run failed: {e}")

    def _notify_reflection(self, proposal) -> None:
        """Best-effort Telegram ping summarizing proposed lessons/questions for review."""
        try:
            from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository
            from src.remote.telegram.telegram_client import get_telegram_client

            chat_id = TelegramChatRepository().get_default_chat_id()
            if not chat_id:
                self._logger.info("[ REFLECTION ] No default Telegram chat — skipping notify.")
                return

            lines = [f"🧠 <b>Reflection</b>: {len(proposal.admitted)} lesson(s) proposed, "
                     f"{len(proposal.questions)} question(s)."]
            for l in proposal.admitted[:5]:
                lines.append(f"• [{l.get('scope')}] {str(l.get('lesson'))[:160]}")
            for q in proposal.questions[:3]:
                lines.append(f"❓ {str(q.get('question'))[:160]}")
            lines.append("\nReview in the app — Reflection panel (or CLI: "
                        "<code>python -m src.core.intelligence.reflection --write</code>)")

            get_telegram_client().send_message(chat_id, "\n".join(lines))
        except Exception as e:
            self._logger.warning(f"[ REFLECTION ] Telegram notify failed: {e}")

    def _get_default_checklist_text(self) -> str:
        """Standard default heartbeat checklist from PromptRegistry."""
        try:
            from src.infrastructure.repositories.prompt_registry import PromptRegistry
            return PromptRegistry.get_instance().get_prompt("heartbeat")
        except Exception:
            return "- Check Gmail for urgent unread emails\n- Check Google Calendar for upcoming events\n- Call `stay_silent` if nothing requires reaching out\n"

