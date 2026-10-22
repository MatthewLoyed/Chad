import asyncio
import contextvars
import hashlib
import logging
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
from src.shared.logger import setup_logger
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.database import local_store
from src.core.intelligence.query_synthesizer import QuerySynthesizer

logger = setup_logger("SemanticToolSelector")

# Sub-queries are independent Supabase RPC + embedding calls — run them concurrently
# instead of one-at-a-time. Small pool: decomposition rarely produces more than a
# handful of sub-intents, and the RPC/embedding work is I/O- and CPU-light per call.
_SUB_QUERY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rag-sub-query")

# Fire-and-forget local preamble generation (issue #541) — upgrades a tool's local RAG
# embedding from a raw description to a richer INTENT/QUERIES/KEYWORDS preamble in the
# background, never blocking the turn that triggered the cache miss. Small pool: this is
# a one-time cost per new/changed tool, not a per-query cost.
_LOCAL_PREAMBLE_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="local-preamble-gen")
_PREAMBLE_GENERATION_INFLIGHT: set = set()
_PREAMBLE_GENERATION_LOCK = threading.Lock()

# Calibrated for nomic-embed-text-v2-moe on 2026-05-17
# Previous v1.5 values: cosine=0.04, rrf=0.003
COSINE_THRESHOLD = 0.04
RRF_THRESHOLD = 0.0018

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        return lambda func: func



class SemanticToolSelector:
    """
    Intelligent tool selection engine using Vector RAG and local semantic fallback.
    Handles query decomposition and multi-intent matching.
    """

    def __init__(self, model_name: str = 'nomic-ai/nomic-embed-text-v2-moe', decompose_queries: bool = True):
        self._model_name = model_name
        self._decompose_queries = decompose_queries
        self._model = None
        self._lock = threading.Lock()
        logger.info("[ SEMANTIC_SELECTOR ] Initialized (Lazy-Loading enabled: model will be loaded on-demand on first RAG query).")

    def _ensure_model(self):
        """Lazy load the embedding model, loading the local SentenceTransformer directly."""
        with self._lock:
            if self._model is None:
                from src.core.intelligence.local_embeddings import get_embedding_model
                self._model = get_embedding_model(self._model_name)
                self._model.ensure_local_transformer()

    def embed_text(self, text: str, *, kind: str = "query") -> Optional[List[float]]:
        """Embed a single string with the same nomic model used for RAG.

        `kind` picks the nomic instruction prefix ("query" → "search_query: ",
        "document" → "search_document: "). Returns a plain list[float] (pgvector-ready)
        or None on any failure — callers treat None as "embedding unavailable" and skip,
        never raise. Reused by the capability-gap analyzer so it doesn't load a second
        copy of the embedding model.
        """
        if not text:
            return None
        try:
            self._ensure_model()
            if not self._model:
                return None
            prefix = "search_document: " if kind == "document" else "search_query: "
            raw = self._model.encode(f"{prefix}{text}")
            if raw is None:
                return None
            return raw.tolist() if hasattr(raw, "tolist") else list(raw)
        except Exception as e:
            logger.error(f"[ SEMANTIC_SELECTOR ] embed_text failed: {e}")
            return None

    @traceable(run_type="retriever", name="SemanticToolSelector")
    def retrieve_relevant_tools(self, query: str, available_tools: Dict[str, Any], k: int = 20, history: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """
        Retrieves semantically relevant tool metadata for a query.
        Prioritizes Supabase RAG (Split-and-Search), falls back to local.
        """
        # 1. Decompose query into sub-intents (Essential for complex queries)
        # We decompose BEFORE enriching to prevent historical context (like 'and') 
        # from creating spurious sub-intents.
        if self._decompose_queries:
            sub_queries = QuerySynthesizer.decompose(query)
        else:
            sub_queries = [query]

        # 2. Query Supabase RAG and prune locally per sub-intent
        # Retrieve candidates for each sub-intent and relative-prune them against their local maximum
        SUB_INTENT_K = 10
        enriched_sub_queries = [
            QuerySynthesizer.enrich_with_history(sub_query, history) for sub_query in sub_queries
        ]
        per_query_results = self._run_sub_queries_parallel(enriched_sub_queries, k=SUB_INTENT_K, available_tools=available_tools)

        pruned_sub_intent_results = []
        for sub_results in per_query_results:
            if not sub_results:
                continue

            sub_highest = sub_results[0].get('similarity', 0.0)
            if sub_highest < 0.1:
                sub_delta = RRF_THRESHOLD
            else:
                sub_delta = COSINE_THRESHOLD
                
            for res in sub_results:
                score = res.get('similarity', 0.0)
                if (sub_highest - score) <= sub_delta:
                    pruned_sub_intent_results.append(res)
                else:
                    break

        if not pruned_sub_intent_results:
            return []

        # 3. Global Deduplication & Ranking
        # We keep the entry with the highest score if it appears in multiple sub-intents
        unique_tools = {}
        for res in pruned_sub_intent_results:
            name = res.get('name')
            if available_tools is not None and name not in available_tools:
                continue
            score = res.get('similarity', 0.0)
            
            if name not in unique_tools or score > unique_tools[name].get('similarity', 0):
                unique_tools[name] = res

        # Sort all tools by their highest found score
        all_results = sorted(unique_tools.values(), key=lambda x: x.get('similarity', 0), reverse=True)
        
        logger.info(f"[ RAG ] Deduplicated local pruned sets: {len(pruned_sub_intent_results)} -> {len(all_results)} tools from {len(sub_queries)} sub-intents.")
        return all_results[:k]

    @traceable(run_type="retriever", name="SemanticToolShortlist")
    def retrieve_shortlist(self, query: str, available_tools: Dict[str, Any], k: int = 50,
                           history: Optional[List[Any]] = None) -> List[str]:
        """
        Recall-oriented candidate shortlist for the HYBRID selector.

        Unlike `retrieve_relevant_tools` (which relatively-prunes to a tight, high-precision
        set), this NARROWS the full catalog to the top-k most relevant tools WITHOUT the
        aggressive delta pruning — the goal is to drop obvious irrelevance while keeping every
        plausibly-needed tool, so the downstream 9B picks from a small but complete menu.

        Returns an ordered list of tool NAMES (best first). Empty list signals RAG unavailable
        so the caller can fall back to handing the LLM the full catalog.
        """
        if self._decompose_queries:
            sub_queries = QuerySynthesizer.decompose(query)
        else:
            sub_queries = [query]

        # Fetch generously per sub-intent so multi-intent queries don't crowd each other out.
        per_intent = max(k, 25)
        enriched_sub_queries = [
            QuerySynthesizer.enrich_with_history(sub_query, history) for sub_query in sub_queries
        ]
        # Recall mode: skip the precision noise-filter so the shortlist doesn't drop a
        # plausibly-needed tool before the 9B ever sees it.
        try:
            _sb = get_supabase_manager()
            _sb_enabled = bool(_sb and getattr(_sb, "enabled", False))
        except Exception as _e:
            _sb_enabled = f"err:{_e}"
        _t_sub0 = time.perf_counter()
        per_query_results = self._run_sub_queries_parallel(
            enriched_sub_queries, k=per_intent, apply_noise_filter=False, available_tools=available_tools
        )
        logger.info(f"[ SHORTLIST-TIMING ] sub_queries={len(enriched_sub_queries)} "
                    f"run_sub_queries={ (time.perf_counter()-_t_sub0)*1000:.0f}ms supabase_enabled={_sb_enabled}")

        best_score: Dict[str, float] = {}
        for sub_results in per_query_results:
            for res in sub_results:
                name = res.get('name')
                if not name or name not in available_tools:
                    continue
                score = res.get('similarity', 0.0)
                if name not in best_score or score > best_score[name]:
                    best_score[name] = score

        if not best_score:
            return []

        # Fair multi-intent merge: round-robin top candidates from each sub-intent
        # so one verbose sub-query (e.g. background Drive/email context) cannot
        # crowd out the user's primary query candidates in the shortlist.
        shortlist: List[str] = []
        seen = set()
        max_depth = max((len(r) for r in per_query_results), default=0)
        for depth in range(max_depth):
            for sub_results in per_query_results:
                if depth < len(sub_results):
                    name = sub_results[depth].get('name')
                    if name and name in available_tools and name not in seen:
                        seen.add(name)
                        shortlist.append(name)
                        if len(shortlist) >= k:
                            break
            if len(shortlist) >= k:
                break

        # If round-robin didn't reach k, fill remaining slots with highest-scoring candidates
        if len(shortlist) < k:
            ranked = sorted(best_score, key=lambda n: best_score[n], reverse=True)
            for name in ranked:
                if name not in seen:
                    seen.add(name)
                    shortlist.append(name)
                    if len(shortlist) >= k:
                        break

        logger.info(f"[ SHORTLIST ] {len(best_score)} candidates -> top {len(shortlist)} "
                    f"(from {len(sub_queries)} sub-intents, catalog={len(available_tools)})")
        return shortlist

    def _run_sub_queries_parallel(self, queries: List[str], k: int,
                                   apply_noise_filter: bool = True,
                                   available_tools: Optional[Dict[str, Any]] = None) -> List[List[Dict[str, Any]]]:
        """
        Run `_query_supabase_rag` for each (already enriched) sub-query concurrently.

        Each call is an independent embedding + Supabase RPC round-trip, so the prior
        one-at-a-time loop paid N round-trip latencies for multi-intent queries even
        though none of them depend on each other. Single sub-query (the common case)
        just runs inline — no pool overhead.

        `_query_supabase_rag` reads/writes `current_turn_tool_queries_var` (a ContextVar),
        which worker threads don't inherit by default — each submission explicitly copies
        the calling context so that contextvar (and the dict it points to) carries over.
        """
        if len(queries) <= 1:
            return [self._query_supabase_rag(q, k=k, apply_noise_filter=apply_noise_filter, available_tools=available_tools)
                    for q in queries]

        # A Context object can't be entered concurrently — even via separate run() calls —
        # so each submission needs its OWN copy, not one shared copy reused across threads.
        futures = [
            _SUB_QUERY_POOL.submit(
                contextvars.copy_context().run, self._query_supabase_rag, q, k, apply_noise_filter, available_tools
            )
            for q in queries
        ]
        return [f.result() for f in futures]

    @traceable(run_type="retriever", name="SupabaseRAGSearch")
    def _query_supabase_rag(self, query: str, k: int, apply_noise_filter: bool = True,
                             available_tools: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Query the tool vector store for semantically relevant tools with comprehensive safeguards.

        Uses the Supabase pgvector RPC (hybrid FTS+vector) when configured; otherwise falls back
        to a local sqlite+numpy cosine search (issue #416) over `available_tools`'s descriptions.

        `apply_noise_filter` (default True) drops low-confidence RRF/vector-only matches — good for
        the precision-oriented standalone RAG path. The HYBRID shortlist passes False: it wants
        RECALL (narrow the catalog without dropping a plausibly-needed tool), and the downstream 9B
        does the final precision pick.
        """
        if not query or not query.strip():
            logger.warning("[ RAG ] Received empty query, skipping search.")
            return []

        from src.shared.config_manager import ConfigManager
        rag_backend = str(ConfigManager.get("tools.rag_backend", "local")).lower()

        # Local-first tool RAG (issue #416, #589, #704):
        # Chad's active tool roster is local in memory (`available_tools`). Relying on a remote cloud
        # database (Supabase) introduces cloud drift: locally registered tools that haven't been
        # synced to Supabase are invisible to tool selection, starving the 3B selector.
        # Local SQLite + numpy cosine similarity operates on the exact live catalog offline with 0 latency.
        if rag_backend == "local" or not available_tools:
            return self._query_local_rag(query, available_tools or {}, k, apply_noise_filter=apply_noise_filter)

        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return self._query_local_rag(query, available_tools or {}, k, apply_noise_filter=apply_noise_filter)

        try:
            # 1. Ensure Model is Loaded with Timeout or Import Safeguards
            try:
                self._ensure_model()
            except Exception as model_err:
                logger.error(f"[ RAG ] Failed to initialize embedding model: {model_err}", exc_info=True)
                return []

            if not self._model:
                logger.error("[ RAG ] Semantic tool model could not be loaded.")
                return []

            try:
                # Format according to nomic v1.5 guidelines: "search_query: "
                query_text = f"search_query: {query}"
                _emb_t0 = time.perf_counter()
                raw_emb = self._model.encode(query_text)
                _embed_ms = (time.perf_counter() - _emb_t0) * 1000.0
                if raw_emb is None:
                    raise ValueError("Model returned null embedding")

                if hasattr(raw_emb, "tolist"):
                    query_embedding = raw_emb.tolist()
                else:
                    query_embedding = raw_emb
            except Exception as emb_err:
                logger.error(f"[ RAG ] Embedding generation failed for '{query}': {emb_err}", exc_info=True)
                return []

            # 3. Execute Supabase RPC Call with Validated Input
            if not isinstance(query_embedding, list) or len(query_embedding) == 0:
                logger.error(f"[ RAG ] Generated invalid embedding list format: {type(query_embedding)}")
                return []

            # Limit RPC call match_count safety
            match_count = max(1, min(int(k), 50))

            _rpc_t0 = time.perf_counter()
            results = supabase.rpc(
                "retrieve_relevant_tools",
                {
                    "query_text": query,
                    "query_embedding": query_embedding,
                    "match_count": match_count
                }
            )
            _rpc_ms = (time.perf_counter() - _rpc_t0) * 1000.0
            # Split the pre-9B RAG cost so "time to reach the 9B" is attributable to
            # local CPU embedding vs Supabase network RPC (no CUDA on this box → embed is CPU-bound).
            logger.info(f"[ RAG ] embed={_embed_ms:.0f}ms rpc={_rpc_ms:.0f}ms query={query[:40]!r}")

            # 4. Validate & Clean Response Formats
            if not results:
                logger.debug(f"[ RAG ] No RPC matches found for sub-query: '{query}'")
                return []

            if not isinstance(results, list):
                logger.error(f"[ RAG ] Malformed RPC response from Supabase: {type(results)}")
                return []

            # Basic data cleaning and mapping
            from src.core.orchestration.context import current_turn_tool_queries_var
            ctx = current_turn_tool_queries_var.get()

            validated_results = []
            for r in results:
                if isinstance(r, dict) and 'name' in r:
                    # Noise cancellation: Filter out low-confidence RRF matches
                    # that don't have a strong raw vector match or a keyword match.
                    # RRF < 0.017 means it only appeared in one of the searches.
                    # raw_similarity between 0.0 and 0.35 means it was a vector-only match with low semantic similarity.
                    similarity = r.get('similarity', 0.0)
                    raw_sim = r.get('raw_similarity')
                    if raw_sim is None:
                        raw_sim = 0.0
                    
                    if apply_noise_filter and similarity < 0.017 and 0.0 < raw_sim < 0.35:
                        logger.debug(f"[ RAG ] Dropping noise match '{r['name']}' (RRF: {similarity:.4f}, Raw: {raw_sim:.4f})")
                        continue

                    validated_results.append(r)
                    tool_name = r['name']
                    if ctx is not None and tool_name not in ctx:
                        ctx[tool_name] = (query, query_embedding)
            
            return validated_results

        except Exception as global_err:
            logger.error(f"[ RAG ] Unexpected system failure in Supabase tool search: {global_err}", exc_info=True)
            return []

    @staticmethod
    def _tool_raw_description(tool: Any) -> str:
        """Best-effort raw description text for a tool (schema/docstring), independent of name."""
        if hasattr(tool, "get_schema"):
            try:
                return (tool.get_schema() or {}).get("description", "") or ""
            except Exception:
                return ""
        elif isinstance(tool, dict):
            return tool.get("description") or (tool.get("schema") or {}).get("description", "") or ""
        return getattr(tool, "description", "") or getattr(tool, "__doc__", "") or ""

    @classmethod
    def _tool_description_text(cls, name: str, tool: Any) -> str:
        """Best-effort description text for a tool, used as the embedding document."""
        return f"{name}: {cls._tool_raw_description(tool)}".strip()

    def _schedule_local_preamble_generation(self, name: str, description: str) -> None:
        """Fire-and-forget: generate a richer local-model preamble for `name` and, on
        success, re-embed and cache it — never blocks the calling turn. See
        PreambleGenerator (issue #541) for the local-vs-static-fallback generation logic.

        LiteLLMAdapter shares one process-global `litellm.aclient_session` httpx client
        across every instance, bound to whichever event loop first created it (the main
        app loop). Spinning up an unrelated `asyncio.run()` loop in this worker thread
        would hand that client to a second, independent loop — a real cross-loop hazard.
        So: if a loop is already running on the calling (main) thread when this is
        scheduled, run the generation coroutine on THAT loop via
        `run_coroutine_threadsafe` instead of creating a new one. Only fall back to a
        fresh `asyncio.run()` loop when nothing was running yet (e.g. a script/test
        context), where no such shared client could already be loop-bound.
        """
        try:
            caller_loop = asyncio.get_running_loop()
        except RuntimeError:
            caller_loop = None

        with _PREAMBLE_GENERATION_LOCK:
            if name in _PREAMBLE_GENERATION_INFLIGHT:
                return
            _PREAMBLE_GENERATION_INFLIGHT.add(name)

        def _run():
            try:
                from src.infrastructure.tools.cloud.preamble_generator import PreambleGenerator, _static_fallback

                coro = PreambleGenerator().generate_preamble(name, description)
                if caller_loop is not None and caller_loop.is_running():
                    preamble = asyncio.run_coroutine_threadsafe(coro, caller_loop).result(timeout=120)
                else:
                    preamble = asyncio.run(coro)
                if not preamble or preamble == _static_fallback(name):
                    # No local model available / generation failed — nothing to upgrade to.
                    return

                self._ensure_model()
                if not self._model:
                    return
                text = f"{name}: {preamble}"
                raw_emb = self._model.encode(f"search_document: {text}")
                if raw_emb is None:
                    return
                blob = local_store.embedding_to_blob(raw_emb.tolist() if hasattr(raw_emb, "tolist") else list(raw_emb))

                conn = local_store.local_db_connect()
                try:
                    # Deliberately leaves description_hash untouched — it tracks the raw
                    # source description, not the derived preamble, so this update can't
                    # cause the next _ensure_local_tool_embeddings pass to see a "changed"
                    # tool and downgrade the embedding straight back to description-only.
                    conn.execute(
                        "UPDATE tool_embeddings SET preamble=?, embedding=?, updated_at=? WHERE name=?",
                        (preamble, blob, datetime.now().isoformat(), name),
                    )
                    conn.commit()
                finally:
                    conn.close()
                logger.info(f"[ RAG ] Local preamble generated + re-embedded for tool: {name}")
            except Exception as e:
                logger.warning(f"[ RAG ] Background local preamble generation failed for {name}: {e}")
            finally:
                with _PREAMBLE_GENERATION_LOCK:
                    _PREAMBLE_GENERATION_INFLIGHT.discard(name)

        _LOCAL_PREAMBLE_POOL.submit(_run)

    def _ensure_local_tool_embeddings(self, available_tools: Dict[str, Any]) -> None:
        """Compute+cache local embeddings for any tool that's new or whose description changed.

        Cached in the local sqlite `tool_embeddings` table, keyed by name with a description
        hash so a tool's docstring/description edit invalidates its cached embedding. Tools
        with a curated MANUAL_PREAMBLES entry are seeded immediately with their high-fidelity
        document representation without waiting for or needing background model calls.
        """
        if not available_tools:
            return
        self._ensure_model()
        if not self._model:
            return

        from src.infrastructure.tools.cloud.preamble_generator import MANUAL_PREAMBLES

        conn = local_store.local_db_connect()
        try:
            existing = {
                row["name"]: (row["description_hash"], row["preamble"])
                for row in conn.execute("SELECT name, description_hash, preamble FROM tool_embeddings")
            }
            to_embed = []
            preamble_candidates = []
            for name, tool in available_tools.items():
                description = self._tool_raw_description(tool)
                manual_pre = MANUAL_PREAMBLES.get(name)
                if manual_pre:
                    text = f"{name}: {manual_pre}".strip()
                    preamble_val = manual_pre
                else:
                    text = f"{name}: {description}".strip()
                    preamble_val = None

                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                cached_hash, cached_preamble = existing.get(name, (None, None))
                if cached_hash != digest:
                    to_embed.append((name, text, digest, preamble_val))
                    if not manual_pre:
                        preamble_candidates.append((name, description))
                elif not cached_preamble and not manual_pre:
                    preamble_candidates.append((name, description))

            if to_embed:
                now = datetime.now().isoformat()
                for name, text, digest, preamble_val in to_embed:
                    raw_emb = self._model.encode(f"search_document: {text}")
                    if raw_emb is None:
                        continue
                    blob = local_store.embedding_to_blob(raw_emb.tolist() if hasattr(raw_emb, "tolist") else list(raw_emb))
                    conn.execute(
                        "INSERT INTO tool_embeddings (name, description_hash, preamble, embedding, updated_at) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(name) DO UPDATE SET description_hash=excluded.description_hash, "
                        "preamble=COALESCE(excluded.preamble, tool_embeddings.preamble), "
                        "embedding=excluded.embedding, updated_at=excluded.updated_at",
                        (name, digest, preamble_val, blob, now),
                    )
                conn.commit()
                logger.info(f"[ RAG ] Local tool embedding cache: computed {len(to_embed)} new/changed of {len(available_tools)} tools.")

            from src.shared.config_manager import ConfigManager
            if ConfigManager.get("tools.generate_preambles", False):
                for name, description in preamble_candidates:
                    self._schedule_local_preamble_generation(name, description)
        finally:
            conn.close()

    def _query_local_rag(self, query: str, available_tools: Dict[str, Any], k: int,
                         apply_noise_filter: bool = True) -> List[Dict[str, Any]]:
        """Local cosine-similarity tool RAG (issue #416, #704).

        Embeddings are cached per-tool in sqlite (see `_ensure_local_tool_embeddings`), so only
        new/changed tools pay the embedding cost on any given call — steady-state this is just a
        query embedding + an in-process numpy cosine scan over a few hundred cached vectors.

        When `apply_noise_filter` is True (standalone precision RAG), matches below COSINE_THRESHOLD
        (0.04) are dropped. When False (recall shortlist for downstream LLM selector), all top-k
        are retained so candidate tools aren't prematurely eliminated.
        """
        if not available_tools:
            return []
        try:
            _t_ens0 = time.perf_counter()
            self._ensure_local_tool_embeddings(available_tools)
            _ensure_ms = (time.perf_counter() - _t_ens0) * 1000.0
            self._ensure_model()
            if not self._model:
                return []

            _t_emb0 = time.perf_counter()
            raw_emb = self._model.encode(f"search_query: {query}")
            _emb_ms = (time.perf_counter() - _t_emb0) * 1000.0
            if raw_emb is None:
                return []
            query_embedding = raw_emb.tolist() if hasattr(raw_emb, "tolist") else list(raw_emb)

            _t_srch0 = time.perf_counter()
            conn = local_store.local_db_connect()
            try:
                placeholders = ",".join("?" for _ in available_tools)
                # Prefer the OATS-refined vector when present, mirroring the Supabase RPC's
                # COALESCE(embedding_refined, embedding) so local search picks up refinement (#470).
                rows = conn.execute(
                    "SELECT name, COALESCE(embedding_refined, embedding) AS embedding "
                    f"FROM tool_embeddings WHERE name IN ({placeholders})",
                    list(available_tools.keys()),
                ).fetchall()
            finally:
                conn.close()

            threshold = COSINE_THRESHOLD if apply_noise_filter else 0.0
            results = local_store.cosine_topk(query_embedding, rows, "embedding", k, match_threshold=threshold)
            _srch_ms = (time.perf_counter() - _t_srch0) * 1000.0
            # Split the local RAG cost per sub-query so we can see if the 100s+ shortlist is
            # ensure-embeddings (catalog re-embed), query encode, or the sqlite/cosine search.
            logger.info(f"[ RAG-LOCAL ] ensure_embeds={_ensure_ms:.0f}ms encode={_emb_ms:.0f}ms "
                        f"search={_srch_ms:.0f}ms rows={len(rows)} matches={len(results)} q={query[:30]!r}")

            # Record (query, embedding) per matched tool so the event wrapper can log an
            # OATS outcome locally when the tool runs — the Supabase path does the same in
            # `_query_supabase_rag`. Without this, offline outcomes would never be captured.
            from src.core.orchestration.context import current_turn_tool_queries_var
            ctx = current_turn_tool_queries_var.get()
            if ctx is not None:
                for r in results:
                    tool_name = r.get("name")
                    if tool_name and tool_name not in ctx:
                        ctx[tool_name] = (query, query_embedding)

            return results
        except Exception as e:
            logger.error(f"[ RAG ] Local tool RAG fallback failed: {e}", exc_info=True)
            return []
