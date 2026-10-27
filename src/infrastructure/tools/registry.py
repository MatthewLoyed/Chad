import logging
import asyncio
from typing import Dict, List, Optional, Any
from src.shared.logger import setup_logger
from .execution.executor_strategy import ExecutionRoutingEngine
from .selection.semantic_selector import SemanticToolSelector
from .selection.llm_selector import LLMToolSelector
from .discovery.discovery_orchestrator import ToolDiscoveryOrchestrator
from .cloud.supabase_bridge import SupabaseToolSyncBridge
from .config_manager import ToolConfigManager

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        return lambda func: func

logger = setup_logger("ToolRegistry")


def _build_llm_selector():
    """Resolve the selector's model and endpoint together for one-model setup."""
    from .selection.configuration import selector_configuration
    return LLMToolSelector(**selector_configuration())


class ToolRegistry:
    """
    Unified entry point for tool management, discovery, and retrieval.
    Acts as a Facade for specialized sub-systems.
    """

    def __init__(self, event_bus: Optional[Any] = None):
        # Core tool storage
        self._event_bus = event_bus
        self._tools: Dict[str, Any] = {}
        self._stubs: Dict[str, Dict[str, Any]] = {}  # tool_name -> stub_metadata
        self._tool_sources: Dict[str, str] = {}  # tool_name -> 'local'/'skill'/'mcp'
        self._categories: Dict[str, List[str]] = {
            'web': [], 'os': [], 'system': [], 'custom': [],
            'canvas': [], 'workspace': [], 'memory': [], 
            'skills': [], 'mcp': [], 'meta': []
        }

        # Skill management tools registered via _register_skill_tools()

        # MCP Display Templates (Visual polish for the UI)
        self._mcp_visual_templates = {
            "app": "Launching {programPath}...",
            "ui_click": "Clicking element {elementId}...",
            "ui_type": "Typing into element {elementId}...",
            "ui_find": "Searching for {elementName}...",
            "ui_scroll": "Scrolling...",
            "screenshot_control": "Scanning screen for UI elements...",
            "window_management": "Managing windows...",
            "file_save": "Saving file {fileName}...",
            "Snapshot": "Capturing UI snapshot...",
            "Screenshot": "Taking screenshot...",
            "Click": "Clicking screen...",
            "Move": "Moving mouse...",
        }

        # Sub-system components
        self._routing_engine = ExecutionRoutingEngine()
        
        # Load tool settings from config
        from src.shared.config_manager import ConfigManager
        decompose = ConfigManager.get("tools.decompose_queries", True)
        self._selector = SemanticToolSelector(decompose_queries=decompose)
        self._sync_bridge = SupabaseToolSyncBridge()
        self._discovery_orchestrator = ToolDiscoveryOrchestrator(self)
        
        # Mandatory tools injected on every turn regardless of query.
        # Kept intentionally small — the LLM selector picks everything else.
        self._core_tool_names = {
            "ask_user_question",  # structured clarifying questions (Claude-style MCQ UI)
            "search_for_tool",    # always needed when a capability isn't in the selected set
            "list_skills",        # meta-discovery: model can't find skills it doesn't know exist
            "load_skill",         # meta-activation: list_skills' own catalog + description tell the
                                  # model to call this next — if the selector doesn't also happen to
                                  # pick it, the model is stuck with no way to actually activate a
                                  # skill it can already see, and either loops re-calling list_skills
                                  # or hallucinates a workaround (see evals/datasets/agentic_skills_live.jsonl
                                  # skill_load_frontend_design_no_loop, perfect_trace run 019f7c61)
            "call_mcp_tool",      # gateway for long-tail MCP when selector misses
            "manage_connectors",  # query, test, configure, and disconnect connectors
            "run_saved_routine",  # user-saved routines invocable from chat
            "manage_user_memory", # personal knowledge graph CRUD — memory_policy expects this always bound
            "web_search",         # canonical research tool — the planner prompt mandates it, so it must
                                  # always be in the menu or the planner loops on an unsatisfiable contract
        }
        
        self._llm_selector = _build_llm_selector()
        
        # Register skill management tools at init
        self._register_skill_tools()
        self._register_core_system_tools()
        
        # Tool Bundles (Logic to expose groups of related tools together)
        from .selection.bundles import default_bundle_groups
        self._bundle_groups = default_bundle_groups()

        # Auto-inject SKILL.md content when a bundle's tools are selected for the turn.
        self._bundle_skills = {
            "google_drive": "google_drive",
            "gmail_read": "gmail",
            "gmail_compose": "gmail",
            "visual_diagram": "kroki",
            "visual_typst": "typst",
            "visual_marp": "marp",
            "visual_animation": "motion_design",
            "visual_presenton": "presenton",
            "visual_image": "image_generation",
            "youtube_music": "youtube_music",
            "places_maps": "osm-maps",
            "canvas": "canvas",
            "canvas_files": "canvas",
            "canvas_modules": "canvas",
            "canvas_discussions": "canvas",
            "canvas_quizzes": "canvas",
            "canvas_grades": "canvas",
            "finance": "finance",
        }

        # Extracted collaborators (issue #247) — the registry stays a thin
        # coordinator that owns tool storage; these own schema-building and
        # per-turn selection respectively, reaching back into this registry's
        # storage rather than duplicating it.
        from .schema.schema_builder import ToolSchemaBuilder
        from .selection.turn_selector import TurnToolSelector
        self._schema_builder = ToolSchemaBuilder(self)
        self._turn_selector = TurnToolSelector(self)

        self._discovered = False

    async def warmup(self):
        """
        Pre-loads expensive resources (embeddings, tool cache) in the background.
        Call this during system bootstrap.
        """
        logger.info("[ REGISTRY ] Warming up tool infrastructure...")
        # 1. Run discovery to hydrate cache/stubs
        await asyncio.to_thread(self.discover_all)
        # 2. Trigger semantic + LLM selector warmup (non-blocking load for embeddings / 9B model)
        if hasattr(self._selector, '_ensure_model'):
            await asyncio.to_thread(self._selector._ensure_model)
        if hasattr(self, '_llm_selector') and self._llm_selector:
            await asyncio.to_thread(self._llm_selector.warmup)
        logger.info("[ SUCCESS ] Tool registry warmup complete.")

    def _register_skill_tools(self) -> None:
        """Register the skill management tools (list_skills, load_skill) at startup."""
        try:
            from src.tools.system.skills.list_skills import ListSkillsTool
            from src.tools.system.skills.load_skill import LoadSkillTool
            self.register_tool(ListSkillsTool(), category='skills', source='local')
            self.register_tool(LoadSkillTool(), category='skills', source='local')
            logger.info("[ REGISTRY ] Skill management tools registered (list_skills, load_skill).")
        except Exception as e:
            logger.warning(f"[ REGISTRY ] Failed to register skill tools: {e}")

    def _register_core_system_tools(self) -> None:
        """Register core system tools like call_mcp_tool at startup."""
        try:
            from src.tools.system.mcp.call_mcp_tool import CallMCPTool
            from src.tools.system.mcp.suggest_mcp_install import SuggestMCPInstallTool
            from src.tools.system.escalate_to_agent import EscalateToAgentTool
            from src.tools.system.run_saved_routine import RunSavedRoutineTool
            from src.tools.web.browser_scroll import BrowserScrollTool
            from src.tools.web.browser_capture_text import BrowserCaptureTextTool
            from src.tools.system.connectors import ManageConnectorsTool
            self.register_tool(CallMCPTool(registry=self), category='system', source='local')
            self.register_tool(SuggestMCPInstallTool(event_bus=self._event_bus), category='system', source='local')
            self.register_tool(EscalateToAgentTool(), category='system', source='local')
            self.register_tool(RunSavedRoutineTool(registry=self), category='system', source='local')
            # Native (source=local) so it bypasses the "MCP tools are discover-only for
            # subagents" gate, but category='playwright' so it lands in browser_agent's
            # domain tool list alongside the raw Playwright MCP tools (issue #287).
            self.register_tool(BrowserScrollTool(registry=self), category='playwright', source='local')
            self.register_tool(BrowserCaptureTextTool(registry=self), category='playwright', source='local')
            self.register_tool(ManageConnectorsTool(), category='system', source='local')
            logger.info(
                "[ REGISTRY ] Core system tools registered "
                "(call_mcp_tool, suggest_mcp_install, escalate_to_agent, run_saved_routine, browser_scroll, browser_capture_text, manage_connectors)."
            )
        except Exception as e:
            logger.warning(f"[ REGISTRY ] Failed to register core system tools: {e}")

    @property
    def tools(self) -> Dict[str, Any]:
        """Expose tools dictionary for backward compatibility."""
        return self._tools

    def register_tool(self, tool: Any, category: str = 'custom', source: str = 'local', force: bool = False):
        """Register a native tool or skill instance."""
        name = getattr(tool, 'name', None)
        if not name:
            logger.warning(f"Attempted to register tool without a name: {tool}")
            return

        if name in self._tools and not force:
            return

        self._tools[name] = tool
        self._tool_sources[name] = source
        self._add_to_category(name, category)
        
        # logger.debug(f"[ REGISTRY ] Registered {source} tool: {name} ({category})")

    def register_mcp_tool(self, tool_metadata: Dict[str, Any], server: Any, category: str = 'mcp'):
        """Register a tool discovered via an MCP server."""
        name = tool_metadata.get('name')
        if not name:
            logger.error("MCP tool missing 'name' field")
            return

        # Skip if already registered as MCP (dedupe on rescan).
        is_existing_mcp = self._tool_sources.get(name) == 'mcp'
        if name in self._tools and is_existing_mcp:
            return

        # Prefer native local tools over MCP when names overlap.
        if name in self._tools and self._tool_sources.get(name) == 'local':
            existing = self._tools.get(name)
            if not (isinstance(existing, dict) and existing.get('is_stub')):
                logger.debug(f"Skipping MCP tool '{name}' — native local tool already registered")
                return

        # Store metadata with server reference
        mcp_tool = {
            **tool_metadata,
            'mcp_server': server,
            'source': 'mcp'
        }
        
        # Inject display template if known
        if name in self._mcp_visual_templates:
            mcp_tool['display_template'] = self._mcp_visual_templates[name]

        self._tools[name] = mcp_tool
        self._tool_sources[name] = 'mcp'
        self._add_to_category(name, category)
        
        # logger.debug(f"[ REGISTRY ] Registered MCP tool: {name} (from {server.name})")

    def register_stub_tool(self, name: str, schema: Dict[str, Any], load_info: Dict[str, Any], category: str = 'custom', source: str = 'local'):
        """
        Register a 'stub' for a tool. The real tool will only be loaded on execution.
        :param name: Unique name of the tool
        :param schema: OpenAI-compatible schema
        :param load_info: Info needed to load the real tool (e.g., path, class_name)
        """
        if name in self._tools:
            return

        self._stubs[name] = {
            'schema': schema,
            'load_info': load_info,
            'category': category,
            'source': source,
            'is_stub': True
        }
        self._tool_sources[name] = source
        self._add_to_category(name, category)
        # We store the stub in _tools too so RAG/discovery can find it
        self._tools[name] = {"schema": schema, "is_stub": True, "load_info": load_info}

    def _load_stub(self, name: str) -> Any:
        """Hydrate a stub into a real tool instance."""
        stub = self._stubs.get(name) or self._tools.get(name)
        if not stub or not isinstance(stub, dict) or not stub.get('is_stub'):
            return self._tools.get(name)

        load_info = stub.get('load_info', {})
        file_path = load_info.get('file_path')
        class_name = load_info.get('class_name')
        
        if not file_path or not class_name:
            logger.error(f"[ REGISTRY ] Missing load info for stub: {name}")
            return None

        try:
            import importlib.util
            from pathlib import Path
            import sys
            
            # Load module with unique name to prevent collisions with stdlib or existing imports
            path = Path(file_path)
            module_name = f"tool_stub_{name}_{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                # Instantiate class
                tool_class = getattr(module, class_name)
                # Pass config if it's a skill
                if stub.get('source') == 'skill':
                    # SkillScanner passes config to __init__
                    tool_instance = tool_class(config=self._discovery_orchestrator._config)
                else:
                    tool_instance = tool_class()
                
                # Replace stub with real instance
                self._tools[name] = tool_instance
                logger.info(f"[ REGISTRY ] Hydrated stub tool: {name}")
                return tool_instance
        except Exception as e:
            logger.error(f"[ REGISTRY ] Failed to hydrate stub {name}: {e}")
            return None

    def _add_to_category(self, tool_name: str, category: str):
        """Helper to manage category lists."""
        if category not in self._categories:
            self._categories[category] = []
        if tool_name not in self._categories[category]:
            self._categories[category].append(tool_name)

    def _extract_schema(self, name: str, tool: Any) -> Dict[str, Any]:
        """Convert a tool instance or metadata into an OpenAI-compatible schema."""
        return self._schema_builder.extract_schema(name, tool)

    def list_catalog_entries(self, *, max_description_len: int = 300) -> List[Dict[str, Any]]:
        """UI / routine-builder friendly tool list with parameter schemas."""
        return self._schema_builder.list_catalog_entries(max_description_len=max_description_len)

    def discover_all(self, config: Optional[Dict[str, Any]] = None, sync_cloud: bool = False):
        """Run discovery and optionally sync with cloud."""
        if self._discovered:
            return
            
        # Re-initialize orchestrator if config is provided
        if config:
            self._discovery_orchestrator = ToolDiscoveryOrchestrator(self, config=config)

        self._discovery_orchestrator.run_discovery()
        self._discovered = True

        if sync_cloud:
            logger.info("[ REGISTRY ] Cloud sync requested. Running sync_all in background...")
            asyncio.create_task(self._sync_bridge.sync_all(self._tools))

    @traceable(run_type="chain", name="ToolRegistryInjector")
    def get_schemas_for_turn(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
        category: Optional[str] = None,
        on_completions_start=None,
        knowledge_context: Optional[str] = None,
    ) -> Dict[str, List[Any]]:
        """Retrieves relevant tool schemas for a given user query."""
        return self._turn_selector.get_schemas_for_turn(
            query,
            history=history,
            category=category,
            on_completions_start=on_completions_start,
            knowledge_context=knowledge_context,
        )

    def get_schemas_for_skill(self, skill_name: str) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible schemas for all tools bundled with a skill."""
        schemas = []
        matching_tools = set()
        for bundle_key, s_name in self._bundle_skills.items():
            if s_name == skill_name:
                matching_tools.update(self._bundle_groups.get(bundle_key, set()))
        for tool_name in matching_tools:
            tool = self._tools.get(tool_name)
            if tool:
                schemas.append(self._extract_schema(tool_name, tool))
        return schemas

    def get_schemas_for_categories(self, categories: List[str]) -> List[Dict[str, Any]]:
        """Get tool schemas for a list of categories."""
        return self._schema_builder.get_schemas_for_categories(categories)

    def get_all_tools(self) -> List[Any]:
        """Return all registered tool instances/metadata."""
        return list(self._tools.values())

    def get_tool(self, name: str) -> Optional[Any]:
        """Retrieve a specific tool instance by name. Hydrates if it is a stub."""
        tool = self._tools.get(name)
        if isinstance(tool, dict) and tool.get('is_stub'):
            return self._load_stub(name)
        return tool

    async def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """Execute a tool using the appropriate strategy."""
        tool = self._tools.get(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found in registry.")

        # Check if it's a stub and hydrate if necessary
        if isinstance(tool, dict) and tool.get('is_stub'):
            tool = self._load_stub(tool_name)
            if not tool:
                raise ValueError(f"Failed to hydrate stub tool '{tool_name}'.")

        from src.tools.base_tool import BaseTool
        if isinstance(tool, BaseTool) and "tool_registry" not in parameters:
            parameters = {**parameters, "tool_registry": self}

        source = self._tool_sources.get(tool_name, 'local')
        executor = self._routing_engine.route_to_executor(tool)

        # Non-progress guard (src/core/tools/repetition.py). This is the one place
        # every lane passes through — the LangGraph tool node reaches tools via
        # this method, bypassing ToolCoordinator and its LoopDetector entirely.
        # Blocks only an identical call that already failed; everything else is an
        # advisory hint attached to the result.
        from src.core.tools import fault_injection, repetition

        # Measurement hook: OFF unless explicitly armed (env var or the
        # `injecting()` context manager — deliberately no config.yaml key, so a
        # stray committed flag can't make a production Brain fail real calls).
        # Placed before the guard so an injected failure is indistinguishable
        # from a real one to everything downstream — which is the point.
        injected = fault_injection.maybe_fail(tool_name)
        if injected is not None:
            repetition.current_guard().note(tool_name, parameters, False)
            return injected

        guard = repetition.current_guard()
        blocked = guard.check(tool_name, parameters)
        if blocked is not None:
            return blocked

        result = await executor.execute(tool, parameters)

        succeeded = not (isinstance(result, dict) and result.get("success") is False)
        hint = guard.note(tool_name, parameters, succeeded)
        if hint and isinstance(result, dict):
            # A separate key rather than appended to `error`/`result`: this is the
            # harness talking about the CALL PATTERN, not part of the tool's output.
            result = {**result, "harness_hint": hint}
        return result

    def get_fast_track_commands(self) -> List[Dict[str, Any]]:
        """
        Allowlisted '/<alias>' commands that skip the LLM/planner and call execute_tool()
        directly. Source of truth is config tools.fast_track_commands — a tool is only
        reachable here if it's listed there AND actually registered.
        """
        from src.shared.config_manager import ConfigManager
        commands = ConfigManager.get("tools.fast_track_commands", []) or []
        valid = []
        for cmd in commands:
            alias = cmd.get("alias")
            tool_name = cmd.get("tool")
            if not alias or not tool_name:
                continue
            if tool_name not in self._tools:
                logger.warning(f"[ FAST TRACK ] Skipping '{alias}': tool '{tool_name}' not registered.")
                continue
            valid.append({
                "alias": alias,
                "label": cmd.get("label", alias),
                "icon": cmd.get("icon"),
            })
        return valid

    def resolve_fast_track(self, alias: str) -> Optional[Dict[str, Any]]:
        """Look up a fast-track alias against the allowlist. Returns None if not allowlisted
        or the backing tool isn't registered."""
        from src.shared.config_manager import ConfigManager
        commands = ConfigManager.get("tools.fast_track_commands", []) or []
        for cmd in commands:
            if cmd.get("alias") != alias:
                continue
            tool_name = cmd.get("tool")
            if not tool_name or tool_name not in self._tools:
                return None
            return {
                "tool": tool_name,
                "params": cmd.get("params", {}) or {},
                "label": cmd.get("label", alias),
            }
        return None

    def search_tools(self, query: str, limit: int = 5, category: Optional[str] = None, expand_bundles: bool = True) -> List[Dict[str, Any]]:
        """
        Public semantic search for tools with optional category/bundle scoping.
        Used by system tools to discover capabilities on-demand.
        """
        tools_to_search = self._tools
        if category:
            cat_clean = category.lower().strip()
            matched_names = set(self._categories.get(cat_clean, []))

            # Check bundle groups (e.g. "canvas", "ui_automation", "browser", "file_system")
            if cat_clean in self._bundle_groups:
                matched_names.update(self._bundle_groups[cat_clean])

            # Check prefixes / sub-bundles (e.g. "canvas" matches "canvas_files", "canvas_modules")
            for b_name, members in self._bundle_groups.items():
                if b_name.startswith(f"{cat_clean}_") or cat_clean.startswith(f"{b_name}_"):
                    matched_names.update(members)

            # Check tool attributes
            for name, tool in self._tools.items():
                t_cat = getattr(tool, "category", "")
                if isinstance(tool, dict):
                    t_cat = tool.get("category", "")
                if str(t_cat).lower().strip() == cat_clean:
                    matched_names.add(name)

            if matched_names:
                tools_to_search = {n: self._tools[n] for n in matched_names if n in self._tools}
            else:
                logger.warning(f"[ REGISTRY ] Category '{category}' not matched to any registered tools; searching full catalog.")

        selected_metadata = self._selector.retrieve_relevant_tools(query, tools_to_search)

        # In-scope keyword fallback to guarantee immediate matching for explicit terms
        if len(selected_metadata) < limit and tools_to_search:
            q_tokens = [t.lower() for t in query.split() if len(t) > 2]
            existing_names = {m.get('name') for m in selected_metadata}
            for name, tool in tools_to_search.items():
                if name in existing_names:
                    continue
                if hasattr(self._selector, "_tool_raw_description"):
                    desc = self._selector._tool_raw_description(tool).lower()
                elif isinstance(tool, dict):
                    desc = str(tool.get("description", "") or tool.get("schema", {}).get("description", "")).lower()
                else:
                    desc = str(getattr(tool, "description", "")).lower()
                name_clean = name.lower()
                if any(t in name_clean or t in desc for t in q_tokens):
                    selected_metadata.append({"name": name, "similarity": 0.5})
                    existing_names.add(name)
                    if len(selected_metadata) >= limit:
                        break

        # Limit to requested amount
        selected_metadata = selected_metadata[:limit]

        # Expand cohesive bundles (e.g. canvas_modules, canvas_files) so related tools accompany the match
        if expand_bundles and hasattr(self, '_turn_selector') and self._turn_selector:
            matched_names = [m.get('name') for m in selected_metadata if m.get('name')]
            expanded_names = self._turn_selector._expand_bundles(matched_names)
            existing = {m.get('name') for m in selected_metadata}
            for b_name in expanded_names:
                if b_name not in existing and b_name in self._tools:
                    selected_metadata.append({"name": b_name, "similarity": 0.5})
                    existing.add(b_name)

        schemas = []
        for meta in selected_metadata:
            name = meta.get('name')
            tool = self._tools.get(name)

            if tool:
                schemas.append(self._extract_schema(name, tool))
            elif 'schema' in meta:
                schemas.append(meta['schema'])

        return schemas

    async def discover_mcp_servers(self, mcp_registry: Any, sync_cloud: bool = False):
        """
        Backward compatibility for manual MCP server discovery.
        Delegates to the internal MCP scanner and syncs to cloud.
        """
        # Each server's scan is an independent connect+list_tools round-trip (subprocess
        # handshake), so running them concurrently turns N sequential round-trips into
        # the cost of the single slowest one. scan_server catches its own exceptions, so
        # one server failing/timing out can't abort the others.
        servers = list(mcp_registry.get_servers().values())
        await asyncio.gather(
            *[self._discovery_orchestrator._mcp_scanner.scan_server(server, category=server.name) for server in servers]
        )

        logger.info(f"[ SUCCESS ] MCP tool discovery complete. ({self.get_tool_count()} tools total)")

        if sync_cloud and self._tools:
            logger.info("[ REGISTRY ] Cloud sync requested for MCP tools. Running sync_all in background...")
            # Sync newly discovered MCP tools to Supabase
            asyncio.create_task(self._sync_bridge.sync_all(self._tools))

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """Return all registered tool schemas."""
        return self._schema_builder.get_all_schemas()

    def get_tools_by_domain(self, domain: str) -> List[Any]:
        """Retrieve a subset of tool schemas for a specialized agent domain."""
        return self._schema_builder.get_tools_by_domain(domain)

    def get_langchain_tool_by_name(self, name: str) -> Optional[Any]:
        """Retrieve a specific tool by name, wrapped as a LangChain StructuredTool."""
        return self._schema_builder.get_langchain_tool_by_name(name)

    def get_langchain_tools_by_domain(self, domain: str) -> List[Any]:
        """Retrieve a subset of tools for a domain, wrapped as LangChain StructuredTools."""
        return self._schema_builder.get_langchain_tools_by_domain(domain)

    def get_all_langchain_tools(self) -> List[Any]:
        """Returns all registered tools wrapped as LangChain StructuredTools."""
        return self._schema_builder.get_all_langchain_tools()

    def get_tool_count(self) -> int:
        """Return total number of registered tools."""
        return len(self._tools)
