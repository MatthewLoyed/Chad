"""
Activity RAG Client — personal_index search (Chrome, Gmail, Drive activity).

KNOWLEDGE LAYER ARCHITECTURE
-----------------------------
Chad queries four knowledge stores automatically on every non-conversational turn.
All are injected before any LLM call via graphrag_injector_node.

  1. RELATIONAL CONTEXT  (GraphRAGClient — graph_rag_client.py)
     ────────────────────────────────────────────────────────────
     Store  : data/knowledge_graph.json  (local NetworkX, no external services)
     Content: Curated personal facts — who the user is, goals, health, life events,
              relationships, long-term context.
     Source : config/personal_knowledge.md (seed) + runtime manage_user_memory
     Add data: Edit personal_knowledge.md for permanent facts, OR call
               GraphRAGClient.ingest(content, source="my_source") at runtime.

  2. ACTIVITY CONTEXT  (ActivityRAGClient — this file)
     ────────────────────────────────────────────────────────────
     Store  : memories table category="personal_index" — **local sqlite only**
              (never mirrored to Supabase; bulk activity stays on-disk).
              Chrome / Gmail / Drive activity / filesystem catalog all land here.
     Content: High-volume activity logs — Chrome history, Gmail threads,
              Google Drive file accesses. "Did I watch X?", "that email about Y."
     Source : IngestionSyncManager writes records here automatically.
     Add data: Call MemoryEngine.save_memory(content, category="personal_index")

  3. DOCUMENT CONTEXT  (DriveCatalogClient — drive_catalog_client.py)
     ────────────────────────────────────────────────────────────
     Store  : memories category="drive_catalog" — local by default
              (``drive_catalog.storage: local``)
     Content: Drive file/folder name + resolved folder path.
     Source : DriveCatalogIngestor

  4. SESSION HISTORY  (SessionHistoryClient — session_history_client.py)
     ────────────────────────────────────────────────────────────
     Store  : memories category="session_log" — local by default
              (checkpointer follows ``langgraph.checkpointer_storage: local``)
     Content: Past conversation exchanges (User+Chad pairs). "What did we decide about X?"
     Source : SessionHistoryIngestor, reading the local LangGraph checkpoint store.

ADDING ANOTHER KNOWLEDGE SOURCE
---------------------------------
1. Create a class with: async def search(self, query: str) -> Optional[str]
2. Instantiate it in supervisor.py alongside graph_client / activity_client / drive_client.
3. Pass it into make_graphrag_node() and add it to the asyncio.gather() call.
4. Give it its own labeled block in the system prompt (see graphrag_node.py).
"""

import asyncio
import logging
from typing import Optional

from src.core.orchestration.context import domain_var
from src.shared.domain_scope import normalize_domain

logger = logging.getLogger("ActivityRAGClient")


class ActivityRAGClient:
    """
    Async wrapper around MemoryEngine.search_browser_history() for the graphrag injector.

    Queries personal_index (Chrome history, Gmail, Drive activity) and returns
        the top matching results as a formatted bullet list. Uses the same embedding strategy
        as the rest of the memory stack (config: memory.embedding_strategy). Storage follows
        ``memory.storage`` (local-first by default).
    """

    def __init__(self):
        self._engine = None

    def _get_engine(self):
        # Deliberately constructs its own session-agnostic MemoryEngine (session_key=None)
        # rather than receiving the session-scoped instance from system_factory: the
        # personal_index it reads is shared across sessions, and MemoryEngine is cheap to
        # build (repository + EmbeddingProvider, no thread pool). See refs #246.
        if self._engine is None:
            from src.core.intelligence.memory_engine import MemoryEngine
            self._engine = MemoryEngine()
        return self._engine

    async def search(self, query: str, limit: Optional[int] = None,
                     domain: Optional[str] = None) -> Optional[str]:
        """
        Search personal_index for activity relevant to the query.
        Runs search_browser_history() in a thread pool to avoid blocking the event loop.
        Returns a formatted bullet list, or None if no relevant results.

        `domain` scopes results to the active domain agent. It is resolved HERE (async
        context) and handed to the executor explicitly — run_in_executor does not carry
        contextvars into the worker thread, so reading domain_var inside would see None.
        """
        try:
            from src.shared.config_manager import ConfigManager
            from src.core.intelligence.knowledge_context_format import format_knowledge_bullets

            if limit is None:
                limit = ConfigManager.get("knowledge_graph.activity_max_results", 3)
            threshold = ConfigManager.get("knowledge_graph.activity_relevance_threshold", 0.22)
            max_item_chars = ConfigManager.get("knowledge_graph.max_item_chars", 120)

            engine = self._get_engine()
            loop = asyncio.get_event_loop()
            # Fetch extra candidates so similarity filtering still fills the cap.
            fetch_limit = max(limit * 3, 10)
            max_age_days = float(ConfigManager.get("knowledge_graph.activity_max_age_days", 120) or 120)
            scope = normalize_domain(domain if domain is not None else domain_var.get())
            results = await loop.run_in_executor(
                None,
                lambda: engine.search_browser_history(query, limit=fetch_limit, domain=scope),
            )
            if not results:
                return None

            # Down-rank / drop stale activity so year-old email todos don't surface as current.
            results = self._apply_recency_gate(results, max_age_days=max_age_days)

            return format_knowledge_bullets(
                results,
                max_items=limit,
                max_chars=max_item_chars,
                min_similarity=threshold,
                query=query,
            )
        except Exception as e:
            logger.error(f"[ ACTIVITY ] Search failed: {e}")
            return None

    @staticmethod
    def _apply_recency_gate(results: list, *, max_age_days: float) -> list:
        """Prefer recent activity; drop items older than max_age_days when a timestamp exists."""
        from datetime import datetime, timezone

        if max_age_days <= 0:
            return results

        now = datetime.now(timezone.utc)
        kept: list = []
        for item in results:
            ts_raw = item.get("timestamp") or ""
            if not ts_raw:
                kept.append(item)
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = (now - ts).total_seconds() / 86400.0
            except Exception:
                kept.append(item)
                continue
            if age_days > max_age_days:
                continue
            # Soft recency boost for ranking inside format_knowledge_bullets.
            sim = float(item.get("similarity", 0.0) or 0.0)
            decay = 1.0 / (1.0 + (age_days / 30.0))
            item = dict(item)
            item["similarity"] = sim * (0.55 + 0.45 * decay)
            kept.append(item)
        return kept or results  # fall back if everything was too old
