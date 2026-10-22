from __future__ import annotations
import logging
import os
from typing import Optional, List, Dict, Any
from src.core.intelligence.query_synthesizer import QuerySynthesizer
from src.core.orchestration.context import domain_var
from src.shared.domain_scope import metadata_matches_domain, normalize_domain

logger = logging.getLogger("MemoryEngine")

class MemoryEngine:
    """
    Memory facade for Chad. Personal facts are read/written via GraphRAGClient
    (knowledge graph). Activity logs (`personal_index`: Chrome, Gmail, Drive
    activity, filesystem catalog) live in the memories table via MemoryRepository
    and follow ``memory.storage`` (local sqlite primary by default; optional
    Supabase mirror). Supports session-based isolation via session keys
    (OpenClaw pattern). See docs/architecture/MEMORY_AND_CONTEXT.md.
    """

    def __init__(
        self,
        db_path: str = None,
        api_key: str = None,
        session_key: str = None
    ):
        """
        Initialize MemoryEngine.
        """
        from src.infrastructure.repositories.memory_repository import MemoryRepository
        from src.shared.config_manager import ConfigManager
        from src.core.intelligence.embedding_provider import EmbeddingProvider

        self.repository = MemoryRepository()
        self.session_key = session_key

        # Embedding generation is delegated to a focused collaborator (refs #246).
        self._embeddings = EmbeddingProvider(
            embedding_strategy=ConfigManager.get("memory.embedding_strategy", "cloud"),  # cloud, local, hybrid
            local_model_name=ConfigManager.get("memory.local_embedding_model", "nomic-ai/nomic-embed-text-v2-moe"),
            api_key=api_key or os.getenv("GEMINI_API_KEY"),
        )

        logger.info(f"[ MEMORY ] Initialized MemoryEngine (Strategy: {self._embeddings.embedding_strategy})")

    @property
    def session_key(self):
        """Active session scope for all reads/writes.

        Prefers the per-turn contextvar (session_id_var, pinned by
        ConversationOrchestrator.process_request) so retrieval during a turn is scoped
        to *that* turn's session even when a concurrent autonomous turn (cron/heartbeat/
        webhook) runs under a different session. The bare instance attribute alone was a
        shared global that a parallel turn could re-point mid-retrieval, skewing which
        conversation's history/memories a turn reads (issue #208). Falls back to the
        instance default for callers outside any turn (memory UI, background jobs).
        """
        from src.core.orchestration.context import session_id_var
        return session_id_var.get() or self._session_key_default

    @session_key.setter
    def session_key(self, value):
        self._session_key_default = value

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """Generate an embedding for `text`. Delegates to EmbeddingProvider."""
        return self._embeddings.get_embedding(text)

    def get_embeddings(self, texts: List[str]) -> Optional[List[Optional[List[float]]]]:
        """Batch-embed texts (LM Studio / local ST). Prefer this for ingest loops."""
        return self._embeddings.get_embeddings(texts)

    def semantic_search(self, query: str, history: Optional[List[Any]] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Search memories by semantic meaning using True Vector RAG.
        Uses QuerySynthesizer to enrich with history and decompose complex intents.
        Generates query embedding and executes database similarity search.
        """
        # 1. Query Decomposition (Decompose BEFORE enriching to prevent historical words like 'and' from triggering splits)
        from src.shared.config_manager import ConfigManager
        decompose_enabled = ConfigManager.get("memory.decompose_queries", True)
        
        if decompose_enabled:
            sub_queries = QuerySynthesizer.decompose(query)
        else:
            sub_queries = [query]
            
        logger.info(f"[ MEMORY_RAG ] Evaluating {len(sub_queries)} decomposed intent paths for vector retrieval.")
        
        # 2. Multi-Vector Retrieval with Context Enrichment
        temp_results = []
        for sub_q in sub_queries:
            # Prepend context for this specific intent branch
            enriched_sub_q = QuerySynthesizer.enrich_with_history(sub_q, history)
            
            # Get vector representation of the enriched sub-query
            embedding = self.get_embedding(enriched_sub_q)
            
            if embedding:
                try:
                    # Use database RPC to perform cosine similarity matching
                    logger.debug(f"[ MEMORY_RAG ] Executing vector search for intent branch: '{sub_q[:40]}...'")
                    results = self.repository.vector_search_memories(
                        embedding=embedding,
                        match_count=3, # Lower count per-intent to prevent context bloating
                        session_key=self.session_key
                    )
                    if results:
                        temp_results.extend(results)
                except Exception as e:
                    logger.error(f"[ MEMORY_RAG ] Vector search failed for '{sub_q[:40]}': {e}")
        
        # 3. Deduplication & Ranking
        if temp_results:
            unique_memories = {}
            for res in temp_results:
                # Handle potential differences in payload formats between Cloud and local SQLite fallback
                # 'content' is the standard key. Fall back to serializing full record if missing.
                mem_content = res.get('content')
                score = res.get('similarity', 0.0)
                
                if not mem_content:
                    continue
                    
                # Keep highest score if memory matches multiple sub-queries
                if mem_content not in unique_memories or score > unique_memories[mem_content].get('similarity', 0.0):
                    unique_memories[mem_content] = res
            
            # Sort all by similarity rank and slice to top_k
            ranked_results = sorted(unique_memories.values(), key=lambda x: x.get('similarity', 0.0), reverse=True)
            final_subset = ranked_results[:top_k]

            # 4. Map into the standardized structural format required by master for UI/Prompt engines
            return [
                {
                    "id": str(m.get("id")),
                    "content": m.get("content"),
                    "type": m.get("category", "general"),
                    "timestamp": m.get("created_at") or m.get("last_accessed"),
                    "similarity": m.get("similarity")
                }
                for m in final_subset
            ]

        # 5. Fallback to standard keyword search on original query if embedding/RPC yielded zero or failed
        return self.search_memories(query)[:top_k]

    def save_memory(self, content: str, category: str = "general", importance: float = 0.5,
                    metadata: Optional[Dict[str, Any]] = None,
                    domain: Optional[str] = None) -> int:
        """Save a memory via repository, stamped with the active domain-agent scope.

        The scope rides in `metadata.domain` rather than its own column so both storage
        backends keep one `create_memory` signature (see memory_backends.py). `domain`
        defaults to the active turn's scope, so every existing caller is tagged without
        changes; pass it explicitly from work running outside a turn's context, where the
        contextvar reads None and the memory would silently land in the global tier.

        NOTE `category` here is the memory TYPE (general/personal_index/session_log) and is
        unrelated to `domain` — the two are orthogonal. See src/shared/domain_scope.py.
        """
        embedding = self.get_embedding(content)
        resolved = normalize_domain(domain if domain is not None else domain_var.get())
        if resolved:
            metadata = {**(metadata or {}), "domain": resolved}
        return self.repository.create_memory(
            content=content,
            category=category,
            importance=importance,
            embedding=embedding,
            session_key=self.session_key,
            metadata=metadata
        )

    def get_memory(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """Get a single memory by ID."""
        return self.repository.get_memory(memory_id)

    def get_all_memories(self) -> List[Dict[str, Any]]:
        """Get all memories."""
        return self.repository.get_all_long_term_memories(session_key=self.session_key)

    @staticmethod
    def _map_ui_memory(record: Dict[str, Any], storage_label: str = "local") -> Dict[str, Any]:
        return {
            "id": str(record.get("id")),
            "content": record.get("content"),
            "type": record.get("category", "general"),
            "timestamp": record.get("created_at") or record.get("last_accessed"),
            "source": storage_label,
        }

    def _ui_storage_label(self) -> str:
        try:
            return self.repository._memory_storage_mode()
        except Exception:
            return "local"

    def get_recent_short_term(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        UI API - Get recent facts (long-term memories).
        The UI uses this to display the 'Memory Core'.
        """
        memories = self.repository.get_all_long_term_memories(session_key=self.session_key, limit=limit)
        label = self._ui_storage_label()
        return [self._map_ui_memory(m, label) for m in memories]

    def list_memories_by_category(
        self,
        category: str,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List memories for one category (Memory UI sections)."""
        rows = self.repository.list_memories(
            category=category,
            session_key=self.session_key,
            limit=limit,
            offset=offset,
        )
        label = self._ui_storage_label()
        return [self._map_ui_memory(m, label) for m in rows]

    def get_long_term_count(self) -> int:
        """Get total count of long-term memories."""
        stats = self.repository.get_statistics()
        return stats.get("total_memories", 0)

    def search_memories(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search memories via repository and map for UI."""
        memories = self.repository.search_memories(query, session_key=self.session_key, limit=limit)
        label = self._ui_storage_label()
        return [self._map_ui_memory(m, label) for m in memories]

    def update_memory(self, memory_id: int, content: str = None, category: str = None, importance: float = None) -> bool:
        """Update a memory via repository."""
        return self.repository.update_memory(memory_id, content, category, importance)

    def delete_memory(self, memory_id: int) -> bool:
        """Delete a memory via repository."""
        return self.repository.delete_memory(memory_id)

    def purge_all_memories(self) -> Dict[str, Any]:
        """Purge all memories via repository."""
        success = self.repository.clear_all()
        return {"success": success, "deleted_count": -1}

    def search_browser_history(
        self,
        query: str,
        limit: int = 10,
        sort_by: str = "relevance",
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Specialized search for browser history (category 'personal_index').
        Supports sorting by 'relevance' (similarity) or 'recency' (chronological).

        `domain` must be passed EXPLICITLY by async callers: this runs in an executor
        thread, and run_in_executor does not propagate contextvars, so reading domain_var
        here would always see the default. See ActivityRAGClient.search.
        """
        # 1. Fallback to keyword search if Gemini embedding is unavailable
        embedding = self.get_embedding(query)
        if not embedding:
            logger.warning("[ MEMORY ] Embeddings unavailable, using keyword fallback.")
            res = self.repository.search_memories_by_category(
                query,
                category="personal_index",
                session_key=self.session_key,
                limit=max(100, limit * 2),
            )
            res = self._filter_by_domain(res, domain)
            # Default keyword search fallback to recency sorting
            res.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return self._map_and_dedup_history(res, limit)

        # 2. Vector search with expanded candidate pool
        # Fetch more than limit because we filter down to 'personal_index'
        candidate_multiplier = 4 if sort_by == "recency" else 2
        match_count = max(50, limit * candidate_multiplier)

        results = self.repository.vector_search_memories(
            embedding=embedding,
            match_count=match_count,
            session_key=self.session_key
        )

        # 3. Filter strictly for browser history, within the active domain scope
        history_results = [
            r for r in self._filter_by_domain(results, domain)
            if r.get("category") == "personal_index"
        ]

        # 4. Apply adaptive sorting
        if sort_by == "recency":
            history_results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        else:
            history_results.sort(key=lambda x: x.get("similarity", 0.0), reverse=True)

        return self._map_and_dedup_history(history_results, limit)

    def search_session_history(self, query: str, limit: int = 10,
                               domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Specialized search for past conversation exchanges (category 'session_log').
        Deliberate-recall counterpart to the auto-injected SESSION HISTORY block —
        use when the auto-injection's similarity threshold may have missed something.

        Domain-scoped: past exchanges from a Financial thread are as private as the facts
        they contain, so they must not resurface in another domain's turn. `domain` is
        passed explicitly for the executor-thread reason noted on search_browser_history.
        """
        embedding = self.get_embedding(query)
        if not embedding:
            logger.warning("[ MEMORY ] Embeddings unavailable, using keyword fallback.")
            res = self.repository.search_memories_by_category(
                query,
                category="session_log",
                session_key=self.session_key,
                limit=max(50, limit * 2),
            )
            res = self._filter_by_domain(res, domain)
            res.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return self._map_and_dedup_history(res, limit)

        match_count = max(50, limit * 4)
        results = self.repository.vector_search_memories(
            embedding=embedding,
            match_count=match_count,
            session_key=self.session_key,
        )
        session_results = [r for r in self._filter_by_domain(results, domain)
                           if r.get("category") == "session_log"]
        session_results.sort(key=lambda x: x.get("similarity", 0.0), reverse=True)
        return self._map_and_dedup_history(session_results, limit)

    @staticmethod
    def _filter_by_domain(rows: List[Dict[str, Any]],
                          domain: Optional[str]) -> List[Dict[str, Any]]:
        """Drop rows belonging to another domain agent (keeps untagged/global rows)."""
        return [r for r in rows if metadata_matches_domain(r.get("metadata"), domain)]

    def _map_and_dedup_history(self, items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        """Maps fields and deduplicates history items by URL/Content."""
        seen_contents = set()
        deduped = []
        
        for item in items:
            content = item.get("content", "")
            if not content:
                continue
                
            # Basic normalization for deduplication
            if content in seen_contents:
                continue
            seen_contents.add(content)
            
            meta = item.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    import json
                    meta = json.loads(meta) if meta else {}
                except Exception:
                    meta = {}

            deduped.append({
                "content": content,
                "timestamp": item.get("created_at") or item.get("last_accessed"),
                "similarity": round(item.get("similarity", 0.0), 3),
                "metadata": meta,
            })
            
            if len(deduped) >= limit:
                break
                
        return deduped

    def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics for the UI."""
        try:
            stats = self.repository.get_statistics()
            memory_count = stats.get("total_memories", 0)
            categories = stats.get("categories", {})
            
            # Database size is less relevant for cloud, but we'll return mock/scaled value
            return {
                "memory_count": memory_count,
                "categories": categories,
                "db_size_bytes": 0,
                "size_percent": 0
            }
        except Exception as e:
            logger.error(f"[ MEMORY ] Error getting storage stats: {e}")
            return {"memory_count": 0, "categories": {}, "db_size_bytes": 0, "size_percent": 0}

    def get_memory_block(self, query: Optional[str] = None, history: Optional[List[Any]] = None,
                         domain: Optional[str] = None) -> str:
        """Legacy entry point — delegates to get_unified_context."""
        return self.get_unified_context(query=query, history=history, domain=domain)

    def get_unified_context(self, query: Optional[str] = None, history: Optional[List[Any]] = None,
                            domain: Optional[str] = None) -> str:
        """
        Personal context for the local TextMode / non-LangGraph path.

        Queries the local NetworkX knowledge graph only. In the LangGraph supervisor
        path, both graph and activity (``personal_index`` via ``memory.storage``,
        Chrome local-only) are queried in parallel by graphrag_injector_node via
        GraphRAGClient + ActivityRAGClient — see activity_rag_client.py.

        This is the domain-isolation choke point for the NON-graph lanes (plan, voice,
        contact). `domain` falls back to the contextvar, but callers that hop threads
        before reaching here must pass it explicitly — see the pool.submit below, which
        runs the search on a fresh loop in a fresh thread that inherits no context.
        """
        if not query and not history:
            return ""

        # Follow-ups ("our roadmap") need prior-turn nouns — same expansion as injector.
        search_query = query or ""
        if history:
            try:
                from src.core.orchestration.nodes.graphrag_node import build_knowledge_query
                from langchain_core.messages import HumanMessage

                messages = []
                for item in history:
                    if isinstance(item, dict):
                        role = (item.get("role") or "").lower()
                        content = item.get("content") or ""
                        if role in {"user", "human"} and content:
                            messages.append(HumanMessage(content=str(content)))
                    else:
                        messages.append(item)
                if query:
                    messages.append(HumanMessage(content=str(query)))
                search_query = build_knowledge_query(messages) or search_query
            except Exception as e:
                logger.debug(f"[ MEMORY ] knowledge query expand failed: {e}")

        if not search_query:
            return ""

        # Resolved BEFORE the thread hop below: the pool thread runs asyncio.run on a fresh
        # loop and inherits no contextvars, so domain_var would read None there and the
        # domain's own facts would silently vanish from its turns.
        scope = normalize_domain(domain if domain is not None else domain_var.get())

        graph_lines: List[str] = []
        try:
            import asyncio
            from src.core.intelligence.graph_rag_client import GraphRAGClient
            client = GraphRAGClient()
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, client.search(search_query, domain=scope))
                    graph_raw = future.result(timeout=3)
            else:
                graph_raw = loop.run_until_complete(client.search(search_query, domain=scope))
            if graph_raw:
                graph_lines = [l.strip() for l in graph_raw.splitlines() if l.strip()]
        except Exception as e:
            logger.debug(f"[ MEMORY ] Graph search failed: {e}")

        seen: set = set()
        merged: List[str] = []

        for line in graph_lines:
            text = line.lstrip("- ").strip()
            if text and text not in seen:
                seen.add(text)
                merged.append(line if line.startswith("-") else f"- {line}")

        if not merged:
            return "\n### PERSONAL CONTEXT\nNo memories relevant to the prompt were found.\n"

        from src.shared.config_manager import ConfigManager
        max_results = ConfigManager.get("knowledge_graph.max_results", 4)
        merged = merged[:max_results]

        return "\n### PERSONAL CONTEXT\n" + "\n".join(merged) + "\n"

    def clear_short_term(self) -> bool:
        """Clear conversation history (short-term memory)."""
        return self.repository.clear_conversation_history()

    # =========================================================================
    # CONVERSATION HISTORY (Supabase Backend)
    # =========================================================================

    def log_turn(self, role: str, content: str, session_key: str = None):
        """Save a conversation turn to Supabase.

        session_key overrides the instance default for this call. Callers that run
        under concurrent turns (ConversationOrchestrator) pass the turn's own key so a
        parallel turn re-pointing self.session_key can't misfile the write (issue #208).
        """
        sk = session_key or self.session_key
        self.repository.create_message(role=role, content=content, session_key=sk)
        logger.info(f"[ MEMORY ] Logged turn to Supabase for {sk}: {role}")

    def prune_history(self, limit: int = 50):
        """Supabase handles history differently, no-op for now unless explicitly needed."""
        pass

    def has_history(self) -> bool:
        """Check if history exists in Supabase."""
        return self.repository.count_messages() > 0

    def get_history_count(self) -> int:
        """Get history count from Supabase."""
        return self.repository.count_messages()

    def get_history_turns(self, max_turns: int = 20) -> list[dict[str, any]]:
        """Get history turns from Supabase."""
        turns = self.repository.get_conversation_history(session_key=self.session_key, limit=max_turns)
        history = []
        for t in turns:
            history.append({
                "role": t["role"],
                "content": t["content"], # OpenAI compatibility
                "parts": [{"text": t["content"]}] # Gemini compatibility
            })
        return history

    def close(self):
        """No-op for SupabaseManager (uses httpx)."""
        pass
