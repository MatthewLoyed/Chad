"""
Session History Client — vector search over category=session_log for the knowledge injector.

Fourth knowledge source (alongside GraphRAG + ActivityRAG + DriveCatalog). See
activity_rag_client.py architecture guide for how additional sources are wired in.
Rows are populated by SessionHistoryIngestor (src/core/intelligence/ingestion/session_history.py)
reading the local LangGraph checkpoint store.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.core.orchestration.context import domain_var
from src.shared.domain_scope import metadata_matches_domain, normalize_domain

logger = logging.getLogger("SessionHistoryClient")


class SessionHistoryClient:
    """Async search over indexed past-session conversation exchanges (category=session_log)."""

    def __init__(self):
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from src.core.intelligence.memory_engine import MemoryEngine
            self._engine = MemoryEngine()
        return self._engine

    async def search(self, query: str, limit: Optional[int] = None,
                     domain: Optional[str] = None) -> Optional[str]:
        """
        Search session_log memories for past conversation exchanges matching the query.
        Returns a formatted bullet list, or None if nothing relevant.

        Domain-scoped: an exchange logged in a Financial thread is as private as the facts
        it contains. Resolved here rather than in the executor (no contextvar propagation).
        """
        try:
            from src.shared.config_manager import ConfigManager
            from src.core.intelligence.knowledge_context_format import format_knowledge_bullets

            if not ConfigManager.get("session_history.enabled", True):
                return None

            if limit is None:
                limit = int(ConfigManager.get("session_history.max_results", 3) or 3)
            threshold = float(
                ConfigManager.get("session_history.relevance_threshold", 0.25) or 0.25
            )
            max_item_chars = int(ConfigManager.get("knowledge_graph.max_item_chars", 160) or 160)

            engine = self._get_engine()
            loop = asyncio.get_event_loop()
            fetch_limit = max(limit * 4, 16)

            scope = normalize_domain(domain if domain is not None else domain_var.get())
            results = await loop.run_in_executor(
                None, lambda: self._search_sessions(engine, query, fetch_limit, scope)
            )
            if not results:
                return None

            return format_knowledge_bullets(
                results,
                max_items=limit,
                max_chars=max_item_chars,
                min_similarity=threshold,
            )
        except Exception as e:
            logger.error(f"[ SESSION_HISTORY ] Search failed: {e}")
            return None

    @staticmethod
    def _search_sessions(engine, query: str, limit: int, domain: Optional[str] = None) -> list:
        backend = engine.repository
        embedding = engine.get_embedding(query)
        if not embedding:
            rows = backend.search_memories_by_category(
                query, category="session_log", session_key=None, limit=limit
            )
            return [
                {"content": r.get("content", ""), "similarity": 0.5, "metadata": r.get("metadata") or {}}
                for r in rows
                if r.get("content") and metadata_matches_domain(r.get("metadata"), domain)
            ]

        hits = backend.vector_search_memories(embedding, max(limit * 5, 25), None, 0.05)
        sessions = [
            {
                "content": r.get("content", ""),
                "similarity": float(r.get("similarity") or 0.0),
                "metadata": r.get("metadata") or {},
            }
            for r in hits
            if r.get("category") == "session_log" and r.get("content")
            and metadata_matches_domain(r.get("metadata"), domain)
        ]
        if sessions:
            sessions.sort(key=lambda x: x.get("similarity", 0.0), reverse=True)
            return sessions[:limit]

        rows = backend.search_memories_by_category(
            query, category="session_log", session_key=None, limit=limit
        )
        return [
            {"content": r.get("content", ""), "similarity": 0.45, "metadata": r.get("metadata") or {}}
            for r in rows
            if r.get("content") and metadata_matches_domain(r.get("metadata"), domain)
        ]
