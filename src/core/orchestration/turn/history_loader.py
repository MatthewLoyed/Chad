from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.shared.logger import setup_logger

logger = setup_logger("TurnHistoryLoader")


class TurnHistoryLoader:
    """Loads and normalizes conversation history from the chat history repository.

    Centralizes the Supabase-row -> message-dict conversion that previously lived
    (duplicated three times) inside ``TurnCoordinator`` — turn entry, preflight
    compaction reload, and manual force-compaction.
    """

    @staticmethod
    def rows_to_history(db_messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Normalize raw DB rows into the ``{role, content, id, [content_blocks]}``
        message dicts the rest of the turn pipeline expects."""
        history: List[Dict[str, Any]] = []
        for msg in db_messages or []:
            role = msg.get("role")
            db_content = msg.get("content", "")
            m: Dict[str, Any] = {"role": role, "id": msg.get("id")}
            if isinstance(db_content, list):
                m["content_blocks"] = db_content
                m["content"] = next(
                    (b.get("text") for b in db_content if b.get("type") == "text"),
                    "[Attached Content]",
                )
            else:
                m["content"] = str(db_content)
            history.append(m)
        return history

    async def load(self, session_id: str) -> List[Dict[str, Any]]:
        """Fetch non-archived history for ``session_id`` and normalize it.

        Returns an empty list for the anonymous/default session or on any
        retrieval error (logged, non-fatal — the turn proceeds without history).
        """
        if not session_id or session_id == "default":
            return []
        from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository

        repo = ChatHistoryRepository()
        try:
            db_messages = await repo.get_messages(session_id, include_archived=False)
            history = self.rows_to_history(db_messages)
            if history:
                logger.info(f"[ TURN ] Loaded {len(history)} messages for session {session_id}")
            return history
        except Exception as e:
            logger.warning(f"[ TURN ] Failed to retrieve history: {e}")
            return []
