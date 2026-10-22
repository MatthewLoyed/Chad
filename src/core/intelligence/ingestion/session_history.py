"""
Session History Ingestor
Reads accumulated conversation turns out of the local LangGraph checkpoint store and
indexes them into the memory layer (category=session_log) so past sessions become
vector-searchable — "what did we talk about last week?".

v1 scope: local sqlite checkpointer only (SafeAiosqliteSaver, data/local_langgraph_checkpoints.db).
If Chad is running against the Postgres/Supabase checkpointer this ingestor no-ops;
conversation history for that mode isn't indexed yet.

Raw exchanges, not LLM summaries: embedding the raw User+Chad text pair avoids adding
an LLM call (and its failure modes) to a background sync cycle. See docs/architecture/
MEMORY_AND_CONTEXT.md for how this fits the rest of the knowledge layer.
"""

import os
import shutil
import asyncio
import tempfile
import logging
from datetime import datetime, timezone
from typing import Any, List, Dict, Optional, Tuple

from src.core.intelligence.ingestion.base import BaseIngestor

logger = logging.getLogger("SessionHistoryIngestor")

# Per-cycle caps keep a single sync cycle bounded even on the first full backfill;
# the watermark (oldest-first thread ordering) picks up the remainder next cycle.
MAX_ITEMS_PER_CYCLE = 800
MAX_EXCHANGES_PER_THREAD = 30
MAX_CONTENT_CHARS = 800


class SessionHistoryIngestor(BaseIngestor):
    """Indexes past conversation exchanges (User + Chad reply pairs) from the local
    LangGraph checkpoint store into category=session_log memories."""

    def __init__(self, checkpoint_db_path: Optional[str] = None):
        self.checkpoint_db_path = (
            checkpoint_db_path
            or os.getenv("CHAD_LOCAL_CHECKPOINT_DB_PATH")
            or os.path.join("data", "local_langgraph_checkpoints.db")
        )

    def get_source_name(self) -> str:
        return "session_history"

    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not os.path.exists(self.checkpoint_db_path):
            logger.info(
                f"[ SessionHistoryIngestor ] No local checkpoint DB at "
                f"{self.checkpoint_db_path} (Postgres mode, or nothing checkpointed yet)."
            )
            return []

        temp_db_path = None
        try:
            temp_dir = tempfile.gettempdir()
            temp_db_path = os.path.join(
                temp_dir, f"chad_checkpoints_sync_{int(datetime.now().timestamp())}.sqlite"
            )
            # Copy first — the live Brain process may hold this file open.
            shutil.copy2(self.checkpoint_db_path, temp_db_path)

            return asyncio.run(self._extract(temp_db_path, last_sync))
        except Exception as e:
            logger.error(f"[ SessionHistoryIngestor ] Ingestion cycle aborted: {e}", exc_info=True)
            return []
        finally:
            if temp_db_path and os.path.exists(temp_db_path):
                try:
                    os.remove(temp_db_path)
                except Exception as clean_err:
                    logger.warning(f"[ SessionHistoryIngestor ] Failed to remove temp DB: {clean_err}")

    async def _extract(self, db_path: str, last_sync: Optional[datetime]) -> List[Dict[str, Any]]:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        items: List[Dict[str, Any]] = []

        async with aiosqlite.connect(db_path) as conn:
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            cursor = await conn.execute("SELECT DISTINCT thread_id FROM checkpoints")
            thread_ids = [r[0] for r in await cursor.fetchall()]

            thread_data: List[Tuple[str, Optional[datetime], list]] = []
            for thread_id in thread_ids:
                try:
                    tup = await saver.aget_tuple(
                        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                    )
                except Exception as e:
                    logger.warning(f"[ SessionHistoryIngestor ] Failed to load thread {thread_id}: {e}")
                    continue
                if not tup or not tup.checkpoint:
                    continue

                ts = self._parse_ts(tup.checkpoint.get("ts"))
                if last_sync and ts and ts <= last_sync:
                    continue  # thread fully covered by a prior cycle

                messages = (tup.checkpoint.get("channel_values") or {}).get("messages") or []
                if messages:
                    thread_data.append((thread_id, ts, messages))

            # Oldest-first so the sync watermark advances safely under the per-cycle cap.
            thread_data.sort(key=lambda t: t[1] or datetime.min.replace(tzinfo=timezone.utc))

            for thread_id, ts, messages in thread_data:
                turns = self._extract_turns(messages)[-MAX_EXCHANGES_PER_THREAD:]
                iso_ts = (ts or datetime.now(timezone.utc)).isoformat()
                for idx, human_text, assistant_text in turns:
                    content = f"[Session {iso_ts[:10]}] User: {human_text}\nChad: {assistant_text}"
                    content = content[:MAX_CONTENT_CHARS]
                    item_hash = self.compute_hash(f"session_{thread_id}_{idx}_{content}")
                    items.append({
                        "content": content,
                        "importance": 0.3,
                        "metadata": {
                            "source": "session_history",
                            "type": "conversation_exchange",
                            "thread_id": thread_id,
                            "timestamp": iso_ts,
                            "hash": item_hash,
                        },
                    })
                    if len(items) >= MAX_ITEMS_PER_CYCLE:
                        return items

        return items

    @staticmethod
    def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _stringify(content: Any) -> str:
        """Flattens langchain message content (str, or multimodal content-block list) to text."""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return " ".join(p for p in parts if p).strip()
        return str(content or "").strip()

    def _extract_turns(self, messages: List[Any]) -> List[Tuple[int, str, str]]:
        """Pairs each human turn with the assistant text that follows it, skipping
        tool-call-only / empty-content messages so rows stay signal, not noise."""
        turns: List[Tuple[int, str, str]] = []
        n = len(messages)
        i = 0
        while i < n:
            msg = messages[i]
            if getattr(msg, "type", None) == "human":
                human_text = self._stringify(getattr(msg, "content", ""))
                j = i + 1
                assistant_parts = []
                while j < n and getattr(messages[j], "type", None) != "human":
                    if getattr(messages[j], "type", None) == "ai":
                        c = self._stringify(getattr(messages[j], "content", ""))
                        if c:
                            assistant_parts.append(c)
                    j += 1
                assistant_text = " ".join(assistant_parts).strip()
                if human_text and assistant_text:
                    turns.append((i, human_text, assistant_text))
                i = j
            else:
                i += 1
        return turns
