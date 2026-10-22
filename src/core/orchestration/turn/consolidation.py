from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.shared.logger import setup_logger
from src.core.utils.token_counter import TokenCounter

if TYPE_CHECKING:
    from src.core.intelligence.adapters.ai_adapter_base import AIAdapter
    from src.application.events.event_bus import EventBus
    from src.core.utils.consolidation_manager import ConsolidationManager
    from src.core.orchestration.turn.history_loader import TurnHistoryLoader

logger = setup_logger("TurnConsolidator")


class TurnConsolidator:
    """Owns the context-window management for a turn: the preflight token check
    and the "Ship's Log" compaction loop.

    The LangGraph engine is passed per-call (rather than held) because the
    coordinator may swap engines between turns; this keeps the consolidator
    stateless with respect to engine identity.
    """

    def __init__(
        self,
        consolidation_manager: Optional["ConsolidationManager"],
        event_bus: Optional["EventBus"],
        history_loader: "TurnHistoryLoader",
    ) -> None:
        self.consolidation_manager = consolidation_manager
        self.event_bus = event_bus
        self.history_loader = history_loader

    async def preflight(
        self,
        sys_prompt: str,
        tools: List[Any],
        history: List[Dict[str, Any]],
        session_id: str,
        adapter: "AIAdapter",
        langgraph_engine: Optional[Any],
    ) -> List[Dict[str, Any]]:
        """Run ConsolidationManager/TokenCounter checks and trigger compaction if
        thresholds are crossed. Returns the (possibly reloaded) history."""
        if not self.consolidation_manager or not session_id or session_id == "default":
            return history

        try:
            current_tokens = TokenCounter.count_total(
                system_instruction=sys_prompt,
                memory_block="",
                history=history or [],
                tools=[],
            )
            logger.info(f"[ PREFLIGHT ] Projected core tokens: {current_tokens}")

            # Determine context limit from capability registry (local) or compaction profile (cloud)
            from src.core.intelligence.model_capability_registry import get_model_capability_registry

            cap_registry = get_model_capability_registry()
            active_ctx = cap_registry._active_model_id and cap_registry.get_context_window(
                cap_registry._active_model_id
            )
            if active_ctx:
                context_limit = active_ctx
            else:
                context_limit = self.consolidation_manager.limits.get("hard", 150000)

            threshold = int(0.70 * context_limit)

            if current_tokens > threshold:
                logger.warning(
                    f"[ PREFLIGHT ] Projected tokens {current_tokens} exceeds 70% threshold "
                    f"of {context_limit}. Executing auto-compaction."
                )
                compaction_success = await self.run_compaction(
                    session_id, history, adapter, langgraph_engine
                )
                if compaction_success:
                    # Reload history to reflect the archived messages and the new wake message
                    updated_history = await self.history_loader.load(session_id)
                    logger.info(
                        f"[ PREFLIGHT ] Reloaded history after compaction. "
                        f"Messages count: {len(updated_history)}"
                    )
                    return updated_history
        except Exception as e:
            logger.error(f"Preflight token counting/compaction failed: {e}", exc_info=True)

        return history

    async def run_compaction(
        self,
        session_id: str,
        history: List[Dict[str, Any]],
        adapter: "AIAdapter",
        langgraph_engine: Optional[Any],
    ) -> bool:
        """
        Runs the Ship's Log Protocol:
        1. Token-based split of history.
        2. Extraction and summarization using LLM with temperature=0.1.
        3. Supabase updates: archive old messages and append wake summary system message.
        4. Reset checkpointer (delete checkpoints).
        5. SQLite MemoryEngine persistence (handled gracefully if it fails).
        """
        if not self.consolidation_manager:
            return False

        logger.info(f"[ CONSOLIDATION ] Starting compaction loop for session {session_id}...")

        # 1. Token-based split of history
        from src.shared.config_manager import ConfigManager

        keep_tokens = ConfigManager.get("consolidation.keep_tokens", 20000)

        to_compact, to_keep = self.consolidation_manager.split_history_by_tokens(history, keep_tokens)
        if not to_compact:
            logger.info(
                "[ CONSOLIDATION ] No older messages exceed keep_tokens boundary. Skipping compaction."
            )
            return False

        logger.info(
            f"[ CONSOLIDATION ] Splitting history: {len(to_compact)} to compact, "
            f"{len(to_keep)} to keep."
        )

        # 2. Extraction and summarization (adapter wrapper)
        async def model_generate_fn(
            prompt: str, history: List[Dict[str, Any]], json_mode: bool = False
        ) -> str:
            transcript = ""
            for msg in history:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                transcript += f"[{role.upper()}]: {content}\n"

            messages = [
                {
                    "role": "system",
                    "content": (
                        f"{prompt}\n\nHere is the conversation history transcript to analyze:"
                        f"\n\n{transcript}"
                    ),
                },
                {
                    "role": "user",
                    "content": "Analyze the transcript and generate the required JSON structure.",
                },
            ]

            kwargs = {}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            res = await adapter.generate(messages=messages, temperature=0.1, **kwargs)
            return res.text or "{}"

        try:
            # Perform enhanced extraction with safeguards (but DO NOT persist to SQLite yet)
            consolidation_result = await self.consolidation_manager.perform_consolidation_with_safeguards(
                history=to_compact,
                model_generate_fn=model_generate_fn,
                persist=False,
            )
        except Exception as e:
            logger.error(f"[ CONSOLIDATION ] LLM summarization failed: {e}", exc_info=True)
            return False

        wake_message = consolidation_result.get("wake_message", "")
        summary = consolidation_result.get("summary", {})
        safeguards = consolidation_result.get("safeguards")

        # 3. Supabase updates: archive old messages and append wake summary system message
        from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository

        repo = ChatHistoryRepository()

        # Get IDs of messages to archive
        message_ids = [msg["id"] for msg in to_compact if msg.get("id")]
        if not message_ids:
            logger.warning(
                "[ CONSOLIDATION ] No message database IDs found in history to archive. "
                "Skip Supabase archive."
            )
        else:
            logger.info(f"[ CONSOLIDATION ] Archiving {len(message_ids)} messages in Supabase...")
            archive_success = await repo.archive_messages(message_ids)
            if not archive_success:
                logger.error(
                    "[ CONSOLIDATION ] Failed to archive messages in Supabase. Aborting compaction."
                )
                return False

        # Append wake summary system message to Supabase
        logger.info("[ CONSOLIDATION ] Appending wake summary message to Supabase...")
        append_res = await repo.append_message(
            conversation_id=session_id,
            role="system",
            content=wake_message,
        )
        if not append_res:
            logger.error(
                "[ CONSOLIDATION ] Failed to append wake message to Supabase. Aborting compaction."
            )
            return False

        # 4. Reset checkpointer (delete thread checkpoints)
        if langgraph_engine:
            logger.info(f"[ CONSOLIDATION ] Resetting checkpointer for thread {session_id}...")
            await langgraph_engine.clear_checkpoints(session_id)

        # 5. SQLite MemoryEngine persistence (SQLite failure is non-blocking)
        try:
            logger.info("[ CONSOLIDATION ] Saving extracted summary data to SQLite memory engine...")
            self.consolidation_manager._persist_data_with_safeguards(summary, safeguards)
            logger.info("[ CONSOLIDATION ] Local memory engine synchronization succeeded.")
        except Exception as e:
            logger.error(
                f"[ CONSOLIDATION ERROR ] SQLite memory engine synchronization failed: {e}",
                exc_info=True,
            )
            if self.event_bus:
                from src.application.events.events import ErrorOccurred, LogMessage

                self.event_bus.publish(
                    ErrorOccurred(
                        error_message=f"Local memory engine synchronization failed: {e}",
                        error_type="SQLiteSync",
                        source="Consolidation",
                        recoverable=True,
                    )
                )
                self.event_bus.publish(
                    LogMessage(
                        message=f"Local memory engine synchronization failed: {e}",
                        level="error",
                    )
                )

        logger.info(
            f"[ CONSOLIDATION ] Compaction loop successfully completed for session {session_id}."
        )
        return True

    async def force(
        self,
        session_id: str,
        adapter: "AIAdapter",
        langgraph_engine: Optional[Any],
    ) -> bool:
        """Manually force compaction for a session by loading history and running
        the compaction loop."""
        if not session_id or session_id == "default":
            logger.error("[ CONSOLIDATION ] Cannot force compaction for default or empty session ID.")
            return False

        logger.info(f"[ CONSOLIDATION ] Manual compaction forced for session {session_id}.")
        try:
            history = await self.history_loader.load(session_id)
            if not history:
                logger.warning(
                    f"[ CONSOLIDATION ] No history found to compact for session {session_id}."
                )
                return False

            return await self.run_compaction(session_id, history, adapter, langgraph_engine)
        except Exception as e:
            logger.error(f"[ CONSOLIDATION ] Manual force_compaction failed: {e}", exc_info=True)
            return False
