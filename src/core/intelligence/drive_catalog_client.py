"""
Drive Catalog Client — vector search over category=drive_catalog for the knowledge injector.

Third knowledge source (alongside GraphRAG + ActivityRAG). See activity_rag_client.py
architecture guide for how to wire additional sources.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.core.orchestration.context import domain_var
from src.shared.domain_scope import metadata_matches_domain, normalize_domain

logger = logging.getLogger("DriveCatalogClient")


class DriveCatalogClient:
    """Async search over the Drive catalog index (name + folder path embeddings)."""

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
        Search drive_catalog memories for files/folders matching the query.
        Returns a formatted bullet list, or None if nothing relevant.

        Domain-scoped; resolved here rather than in the executor thread, which does not
        inherit contextvars.
        """
        try:
            from src.shared.config_manager import ConfigManager
            from src.core.intelligence.knowledge_context_format import format_knowledge_bullets

            if not ConfigManager.get("drive_catalog.enabled", True):
                return None

            if limit is None:
                limit = int(ConfigManager.get("drive_catalog.max_results", 5) or 5)
            threshold = float(
                ConfigManager.get("drive_catalog.relevance_threshold", 0.50) or 0.50
            )
            max_item_chars = int(
                ConfigManager.get("knowledge_graph.max_item_chars", 160) or 160
            )

            engine = self._get_engine()
            loop = asyncio.get_event_loop()
            fetch_limit = max(limit * 4, 16)

            scope = normalize_domain(domain if domain is not None else domain_var.get())
            results = await loop.run_in_executor(
                None,
                lambda: self._search_catalog(engine, query, fetch_limit, scope),
            )
            if not results:
                return None

            return format_knowledge_bullets(
                results,
                max_items=limit,
                max_chars=max_item_chars,
                min_similarity=threshold,
                query=query,
            )
        except Exception as e:
            logger.error(f"[ DRIVE_CATALOG ] Search failed: {e}")
            return None

    @staticmethod
    def _search_catalog(engine, query: str, limit: int, domain: Optional[str] = None) -> list:
        # Catalog is local-first (drive_catalog.storage); do not hit the general
        # memory backend when that points at Supabase.
        backend = getattr(engine.repository, "catalog_backend", None) or engine.repository
        embedding = engine.get_embedding(query)
        if not embedding:
            rows = backend.search_memories_by_category(
                query, category="drive_catalog", session_key=None, limit=limit
            )
            return [
                {
                    "content": r.get("content", ""),
                    # Keyword rank is not a measured semantic similarity. The
                    # automatic injector must not fabricate confidence for it.
                    "similarity": None,
                    "metadata": r.get("metadata") or {},
                }
                for r in rows
                if r.get("content")
            ]

        hits = backend.vector_search_memories(
            embedding,
            max(limit * 5, 25),
            None,
            0.05,  # raw floor; format_knowledge_bullets applies drive_catalog.relevance_threshold
        )
        catalog = [
            {
                "content": r.get("content", ""),
                "similarity": float(r.get("similarity") or 0.0),
                "metadata": r.get("metadata") or {},
            }
            for r in hits
            if r.get("category") == "drive_catalog" and r.get("content")
            and metadata_matches_domain(r.get("metadata"), domain)
        ]
        if catalog:
            catalog.sort(key=lambda x: x.get("similarity", 0.0), reverse=True)
            return catalog[:limit]

        rows = backend.search_memories_by_category(
            query, category="drive_catalog", session_key=None, limit=limit
        )
        return [
            {
                "content": r.get("content", ""),
                "similarity": None,
                "metadata": r.get("metadata") or {},
            }
            for r in rows
            if r.get("content") and metadata_matches_domain(r.get("metadata"), domain)
        ]
