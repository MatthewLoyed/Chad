import logging
import asyncio
from typing import Dict, Any, List, Optional
from src.shared.logger import setup_logger
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.tools.cloud.preamble_generator import get_description_hash, MANUAL_PREAMBLES

logger = setup_logger("SupabaseToolSyncBridge")

class SupabaseToolSyncBridge:
    """
    Handles synchronization between the local tool registry and Supabase Vector Store.
    Includes Intent Preambles for boosted retrieval accuracy.
    """

    def __init__(self, model_name: str = 'nomic-ai/nomic-embed-text-v2-moe'):
        self._model_name = model_name
        self._model = None
        self._preamble_gen = None

    def _ensure_model(self):
        if self._model is None:
            try:
                logger.info(f"Loading local SentenceTransformer model in sync bridge: {self._model_name}...")
                from sentence_transformers import SentenceTransformer
                # We use trust_remote_code=True for Nomic models
                self._model = SentenceTransformer(self._model_name, trust_remote_code=True)
                logger.info("[ SUCCESS ] Loaded local SentenceTransformer model in sync bridge.")
            except Exception as local_err:
                logger.error(f"[ ERROR ] Failed to load local SentenceTransformer in sync bridge: {local_err}", exc_info=True)
                raise local_err

    async def _async_ensure_model(self):
        if self._model is None:
            logger.info(f"Loading semantic sync model in background: {self._model_name}...")
            await asyncio.to_thread(self._ensure_model)

    def _get_tool_description(self, name: str, tool: Any) -> str:
        """Extracts description from various tool types."""
        if hasattr(tool, 'get_schema'):
            return tool.get_schema().get('description', '')
        elif isinstance(tool, dict) and 'schema' in tool:
            return tool['schema'].get('description', tool.get('description', ''))
        elif isinstance(tool, dict) and 'inputSchema' in tool:
            return tool.get('description', '')
        elif isinstance(tool, dict):
            return tool.get('description', name)
        else:
            return getattr(tool, 'description', str(tool))

    def _get_tool_schema(self, name: str, tool: Any, description: str) -> Dict[str, Any]:
        """Extracts schema from various tool types."""
        if hasattr(tool, 'get_schema'):
            return tool.get_schema()
        elif isinstance(tool, dict) and 'schema' in tool:
            return tool['schema']
        elif isinstance(tool, dict) and 'inputSchema' in tool:
            return {
                "name": tool.get('name', name),
                "description": description,
                "parameters": tool['inputSchema']
            }
        else:
            return {"name": name, "description": description}

    def _get_tool_parameters(self, tool: Any) -> str:
        """Extracts parameters schema as a clean JSON or text summary for LLM context."""
        try:
            schema = {}
            if hasattr(tool, 'get_schema'):
                schema = tool.get_schema()
            elif isinstance(tool, dict) and 'schema' in tool:
                schema = tool['schema']
            elif isinstance(tool, dict) and 'inputSchema' in tool:
                schema = tool['inputSchema']
            
            parameters = schema.get('parameters', schema)
            if not parameters:
                return "None"
            
            import json
            return json.dumps(parameters, indent=2)
        except Exception:
            return "None"

    # Safety floor for `sync_all`'s delete step. A partial/incomplete local registry
    # (e.g. MCP servers not scanned, or a stale discovery cache) looks identical to a
    # genuine bulk removal — the only difference is intent. Refuse to delete past this
    # floor without an explicit override, so a bad discovery run can't silently wipe
    # live production tool embeddings (incident 2026-07-05: a filesystem-only sync script
    # with no MCP scanning deleted ~89 real MCP-sourced tools; removed in favor of
    # src/scripts/refresh_rag_tools.py, which scans MCP servers before syncing).
    MAX_REMOVAL_COUNT = 15
    MAX_REMOVAL_RATIO = 0.15

    async def sync_all(self, tool_map: Dict[str, Any], force_delete: bool = False):
        """
        Synchronizes all local tools to the Supabase vector store.
        Uses Intent Preambles and MD5 caching to minimize LLM calls.

        `force_delete`: bypass the bulk-removal safety floor (MAX_REMOVAL_COUNT /
        MAX_REMOVAL_RATIO). Only pass True when you've verified `tool_map` came from a
        COMPLETE discovery (local tools + all MCP servers scanned live) and the removals
        are genuine. New/updated tools always sync regardless — only deletes are gated.
        """
        if self._preamble_gen is None:
            from src.infrastructure.tools.cloud.preamble_generator import PreambleGenerator
            self._preamble_gen = PreambleGenerator()

        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            logger.warning("Supabase disabled, skipping cloud sync.")
            return

        # 1. Fetch current tools from Supabase to identify orphans and cached preambles
        logger.debug("[ SYNC ] Fetching existing tools to identify orphans and cached metadata...")
        existing_tools_raw = await supabase.async_query("tools", select="name, intent_preamble, description_hash, embedding_model, schema")
        
        # Map by name for easy lookup: {name: {"intent_preamble": str, "description_hash": str, "embedding_model": str, "schema": dict}}
        existing_meta = {
            t['name']: {
                "intent_preamble": t.get('intent_preamble'),
                "description_hash": t.get('description_hash'),
                "embedding_model": t.get('embedding_model'),
                "schema": t.get('schema')
            } for t in existing_tools_raw
        } if existing_tools_raw else {}

        # 2. Identify missing preambles or changed descriptions/schemas/models
        new_tools = []
        updated_tools = []  # List of tuples: (name, reason_str)
        unchanged_tools = []
        removed_tools = []

        # Find removed tools
        for name in existing_meta.keys():
            if name not in tool_map:
                removed_tools.append(name)

        # Classify tools in registry
        tools_needing_preamble = []
        tools_needing_reembedding_only = []
        tools_to_upsert = []  # List of tuples: (name, description, preamble)

        for name, tool in tool_map.items():
            description = self._get_tool_description(name, tool)
            parameters = self._get_tool_parameters(tool)
            current_hash = get_description_hash(description + " | " + parameters)
            new_schema = self._get_tool_schema(name, tool, description)

            cached = existing_meta.get(name)
            if not cached:
                new_tools.append(name)
                tools_needing_preamble.append((name, description, parameters))
            else:
                # Detect manual override change
                manual_override_changed = False
                if name in MANUAL_PREAMBLES:
                    target_preamble = MANUAL_PREAMBLES[name]
                    if cached.get("intent_preamble") != target_preamble:
                        manual_override_changed = True
                
                description_changed = cached.get("description_hash") != current_hash or manual_override_changed
                schema_changed = cached.get("schema") != new_schema
                model_changed = cached.get("embedding_model") != "nomic-v2-moe"
                
                is_fallback = cached.get("intent_preamble") and (
                    cached["intent_preamble"].endswith(f"use the {name} tool.") or
                    cached["intent_preamble"].startswith("INTENT: Use the") or
                    not cached["intent_preamble"].startswith("INTENT:")
                )

                if description_changed or is_fallback:
                    reasons = []
                    if description_changed:
                        reasons.append("description/parameters changed")
                    if is_fallback:
                        reasons.append("static fallback update")
                    updated_tools.append((name, ", ".join(reasons)))
                    tools_needing_preamble.append((name, description, parameters))
                elif model_changed:
                    updated_tools.append((name, "embedding model upgrade"))
                    reusable_preamble = cached.get("intent_preamble")
                    tools_needing_reembedding_only.append((name, description, reusable_preamble))
                elif schema_changed:
                    updated_tools.append((name, "schema parameters changed"))
                    tools_to_upsert.append((name, description, cached.get("intent_preamble")))
                else:
                    unchanged_tools.append(name)

        # Bulk-removal safety gate — see MAX_REMOVAL_COUNT/MAX_REMOVAL_RATIO docstring.
        # Must run BEFORE the diff print so the console output reflects what will
        # actually happen (BLOCKED vs REMOVED), not a misleading preview.
        removal_blocked = False
        if removed_tools and not force_delete:
            removal_ratio = len(removed_tools) / max(len(existing_meta), 1)
            if len(removed_tools) > self.MAX_REMOVAL_COUNT or removal_ratio > self.MAX_REMOVAL_RATIO:
                removal_blocked = True
                logger.error(
                    f"[ SYNC SAFETY ] Refusing to delete {len(removed_tools)}/{len(existing_meta)} "
                    f"tools ({removal_ratio:.0%} of catalog): {removed_tools}. This usually means the "
                    f"local registry is INCOMPLETE (e.g. MCP servers not scanned/connected), not that "
                    f"tools were genuinely removed. Run discovery with a full live MCP scan (see "
                    f"src/scripts/refresh_rag_tools.py) and verify the count before retrying, or pass "
                    f"force_delete=True if this is a confirmed bulk removal. New/updated tools will "
                    f"still sync below."
                )
                removed_tools = []

        # Print a clear diff summary to the console
        print("[refresh_rag_tools] Schema changes detected:")
        for name in new_tools:
            print(f"  NEW:     {name} (new tool)")
        for name, reason in updated_tools:
            print(f"  UPDATED: {name} ({reason})")
        if removal_blocked:
            print(f"  BLOCKED: bulk deletion refused — see error log for details and how to override.")
        for name in removed_tools:
            print(f"  REMOVED: {name} (no longer in registry)")
        print(f"  UNCHANGED: {len(unchanged_tools)} tools (skipped)")

        # If nothing changed, we do not perform any upserts and deletes
        if not new_tools and not updated_tools and not removed_tools:
            logger.info("[ SYNC ] No schema or description changes detected. Skipping all database operations.")
            return

        # Prepare payloads for schema-only/reusable-preamble updates
        all_payloads_to_upsert = []
        if tools_to_upsert:
            await self._async_ensure_model()
            for name, description, cached_preamble in tools_to_upsert:
                p = await asyncio.to_thread(self._prepare_payload, name, tool_map[name], cached_preamble)
                if p:
                    all_payloads_to_upsert.append(p)

        # 3. Handle pure re-embeddings first (extremely fast, bypasses LLM & sleeps completely!)
        if tools_needing_reembedding_only:
            logger.info(f"[ SYNC ] Re-embedding {len(tools_needing_reembedding_only)} tools using cached preambles (Fast Path)...")
            await self._async_ensure_model()
            
            # Encode in chunks of 20 to avoid large batch serialization delays
            CHUNK_SIZE = 20
            for i in range(0, len(tools_needing_reembedding_only), CHUNK_SIZE):
                chunk = tools_needing_reembedding_only[i : i + CHUNK_SIZE]
                reembed_payloads = []
                for name, desc, preamble in chunk:
                    tool_data = tool_map[name]
                    p = await asyncio.to_thread(self._prepare_payload, name, tool_data, preamble)
                    if p:
                        reembed_payloads.append(p)
                
                if reembed_payloads:
                    logger.info(f"[ SYNC ] Upserting fast path re-embeddings chunk {i//CHUNK_SIZE + 1}...")
                    await supabase.async_upsert("tools", reembed_payloads, on_conflict="name")

            logger.info(f"[ SUCCESS ] Fast-path re-embedded {len(tools_needing_reembedding_only)} tools.")

        # 4. Handle tools that ACTUALLY need a new preamble generated
        from src.shared.config_manager import ConfigManager
        is_auto_enabled = ConfigManager.get("tool_sync.automatic_preambles", False)

        # Handle tools needing preamble generation
        if tools_needing_preamble:
            if is_auto_enabled:
                logger.info(f"[ SYNC ] Generating {len(tools_needing_preamble)} new intent preambles via LLM...")
                
                # Use 1 concurrent request with 5s sleep to respect 15 RPM Free Tier
                sem = asyncio.Semaphore(1)
                async def gen_with_sem(name, desc, params):
                    async with sem:
                        preamble = await self._preamble_gen.generate_preamble(name, desc, params)
                        await asyncio.sleep(5.0)
                        return name, preamble

                # Process in chunks of 5
                CHUNK_SIZE = 5
                for i in range(0, len(tools_needing_preamble), CHUNK_SIZE):
                    chunk_tools = tools_needing_preamble[i : i + CHUNK_SIZE]
                    
                    # 1. Generate Preambles
                    gen_tasks = [gen_with_sem(name, desc, params) for name, desc, params in chunk_tools]
                    chunk_results = await asyncio.gather(*gen_tasks)
                    
                    # 2. Encode and Prepare Payloads
                    await self._async_ensure_model()
                    for name, preamble in chunk_results:
                        if preamble:
                            tool_data = tool_map[name]
                            p = await asyncio.to_thread(self._prepare_payload, name, tool_data, preamble)
                            if p: 
                                all_payloads_to_upsert.append(p)
            else:
                logger.info(f"[ SYNC ] Auto-preambles disabled. Syncing {len(tools_needing_preamble)} tools with static fallbacks.")
                # Fast path: Use static preambles without LLM or delays
                await self._async_ensure_model()
                for name, desc, params in tools_needing_preamble:
                    preamble = await self._preamble_gen.generate_preamble(name, desc, params)
                    tool_data = tool_map[name]
                    p = await asyncio.to_thread(self._prepare_payload, name, tool_data, preamble)
                    if p:
                        all_payloads_to_upsert.append(p)

        # Execute all upserts in chunks of 20
        if all_payloads_to_upsert:
            logger.info(f"[ SYNC ] Upserting {len(all_payloads_to_upsert)} tools to Supabase...")
            UPSERT_CHUNK_SIZE = 20
            for i in range(0, len(all_payloads_to_upsert), UPSERT_CHUNK_SIZE):
                chunk = all_payloads_to_upsert[i : i + UPSERT_CHUNK_SIZE]
                await supabase.async_upsert("tools", chunk, on_conflict="name")
            logger.info(f"[ SUCCESS ] Fully upserted all updated/new tool schemas.")

        # Clean up removed tools
        if removed_tools:
            logger.info(f"[ SYNC ] Cleaning up {len(removed_tools)} removed tools from Supabase...")
            for removed in removed_tools:
                await supabase.async_delete("tools", {"name": f"eq.{removed}"})
            logger.info(f"[ SUCCESS ] Removed orphaned tools from Supabase.")

        logger.debug("[ SUCCESS ] Tool synchronization complete.")

    def _prepare_payload(self, name: str, tool: Any, preamble: str) -> Dict[str, Any]:
        """Serializes a tool instance into a Supabase-compatible payload with embeddings."""
        try:
            description = self._get_tool_description(name, tool)
            parameters = self._get_tool_parameters(tool)
            schema = self._get_tool_schema(name, tool, description)

            # Nomic Asymmetric Encoding: search_document prefix for indexing
            # Prepend bare tool name and use the new structured pre-retrieval document
            indexing_text = (
                f"search_document: {name}\n"
                f"{preamble}\n"
                f"{description}"
            )
            embedding = self._model.encode(indexing_text).tolist()

            return {
                "name": name,
                "description": description,
                "intent_preamble": preamble,
                "description_hash": get_description_hash(description + " | " + parameters),
                "schema": schema,
                "embedding": embedding,
                "embedding_model": "nomic-v2-moe"
            }
        except Exception as e:
            logger.error(f"Failed to prepare sync payload for tool '{name}': {e}")
            return {}
