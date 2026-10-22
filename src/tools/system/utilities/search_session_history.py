"""
Search Session History Tool - Deliberate recall over past conversation transcripts.

Hybrid retrieval:
1. Full chat-history transcripts (messages table) via FTS/keyword — source of truth
   for exact phrases, URLs, titles ("that youtube link").
2. Vector ``session_log`` memories (truncated checkpoint exchanges) as semantic
   fallback when chat-history search returns no hits.

Counterpart to the auto-injected SESSION HISTORY block (graphrag_injector_node),
which only surfaces thresholded vector hits on every turn.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display
from src.core.intelligence.memory_engine import MemoryEngine
from src.shared.logger import setup_logger

logger = setup_logger("SearchSessionHistoryTool")

_AGENT_SNIPPET_MAX = 240


def _truncate(text: str, max_chars: int = _AGENT_SNIPPET_MAX) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


@tool_display("Searching past conversations for '{query}'...")
class SearchSessionHistoryTool(BaseTool):
    """Tool for searching past chat transcripts (FTS) with semantic session_log fallback."""

    name = "search_session_history"
    description = (
        "COMMAND: Search past chat transcripts with the user. Prefer this for exact "
        "phrases, URLs, video/link titles, or when the user asks what you discussed "
        "before (e.g. 'that youtube link', 'golfport dinner', 'the budget spreadsheet'). "
        "Returns conversation title, date, role, and a short snippet — not full messages."
    )
    category = "custom"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        memory_engine: Optional[MemoryEngine] = None,
        chat_history_repo: Optional[Any] = None,
    ):
        super().__init__(config=config)
        self.memory = memory_engine if memory_engine else MemoryEngine()
        self._chat_history_repo = chat_history_repo

    def _get_chat_repo(self):
        if self._chat_history_repo is not None:
            return self._chat_history_repo
        from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
        self._chat_history_repo = ChatHistoryRepository()
        return self._chat_history_repo

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to search for — exact phrases, URLs, or topics "
                            "(e.g. 'youtube.com/watch', 'that bug we fixed last week')."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of hits to return (default is 10).",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(self, query: str, limit: int = 10, **kwargs) -> Dict[str, Any]:
        try:
            limit = max(1, min(int(limit or 10), 50))
            results = await self._hybrid_search(query.strip(), limit)
            return {
                "success": True,
                "count": len(results),
                "results": results,
                "caption": f"Found {len(results)} past conversation hit(s) matching '{query}'.",
            }
        except Exception as e:
            logger.error(f"Error in search_session_history: {e}")
            return {"success": False, "error": str(e)}

    async def _hybrid_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        chat_hits = await self._search_chat_history(query, limit)
        formatted: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        for hit in chat_hits:
            key = self._dedupe_key(hit)
            if key in seen:
                continue
            seen.add(key)
            formatted.append(hit)
            if len(formatted) >= limit:
                return formatted

        # Semantic fill only when keyword/FTS found nothing — don't dilute exact hits.
        if formatted:
            return formatted

        vector_hits = await asyncio.to_thread(
            self.memory.search_session_history,
            query=query,
            limit=limit,
        )
        for raw in vector_hits:
            mapped = self._format_vector_hit(raw)
            key = self._dedupe_key(mapped)
            if key in seen:
                continue
            seen.add(key)
            formatted.append(mapped)
            if len(formatted) >= limit:
                break

        return formatted

    async def _search_chat_history(self, query: str, limit: int) -> List[Dict[str, Any]]:
        repo = self._get_chat_repo()
        rows = await repo.search_chat_messages(query, limit=limit)
        out: List[Dict[str, Any]] = []
        for row in rows:
            snippet = row.get("snippet") or _truncate(str(row.get("content") or ""))
            out.append({
                "source": "chat_history",
                "conversation_id": row.get("conversation_id"),
                "conversation_title": row.get("conversation_title") or "Untitled Chat",
                "message_id": row.get("id"),
                "role": row.get("role"),
                "timestamp": row.get("created_at"),
                "snippet": _truncate(str(snippet)),
                "rank": row.get("rank"),
            })
        return out

    @staticmethod
    def _format_vector_hit(raw: Dict[str, Any]) -> Dict[str, Any]:
        meta = raw.get("metadata") or {}
        content = raw.get("content") or ""
        return {
            "source": "session_log",
            "conversation_id": meta.get("thread_id"),
            "conversation_title": None,
            "message_id": None,
            "role": None,
            "timestamp": raw.get("timestamp") or meta.get("timestamp"),
            "snippet": _truncate(str(content)),
            "similarity": raw.get("similarity"),
            "rank": None,
        }

    @staticmethod
    def _dedupe_key(hit: Dict[str, Any]) -> str:
        mid = hit.get("message_id")
        if mid:
            return f"msg:{mid}"
        snip = (hit.get("snippet") or "").strip().lower()
        return f"snip:{snip[:120]}"

    def validate_params(self, query: str = None, **kwargs) -> tuple[bool, str | None]:
        if not query or not query.strip():
            return False, "Query is required for searching session history."
        return True, None
