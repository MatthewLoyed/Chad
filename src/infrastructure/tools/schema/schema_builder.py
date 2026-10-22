"""ToolSchemaBuilder — schema extraction, hardening, and LangChain wrapping.

Extracted from ToolRegistry to keep that class a thin coordinator (issue #247).
Operates against the registry's tool storage (`_tools`, `_categories`,
`_tool_sources`) rather than owning it, so the registry stays the single source
of truth for what is registered.
"""

from typing import Any, Dict, List, Optional

from src.shared.logger import setup_logger
from ..config_manager import ToolConfigManager

logger = setup_logger("ToolSchemaBuilder")

# Full JSON Schema ``parameters`` block from ``BaseTool.get_schema()``, preserved on
# LangChain StructuredTools.  ChatChad reads this when building the tool list for the
# LLM — the flat Pydantic args_model only knows ``list``/``dict`` and strips nested
# ``items`` / ``properties`` (see ``parameters_for_llm_from_tool``).
CHAD_PARAMETERS_SCHEMA_ATTR = "_chad_parameters_schema"


def pydantic_args_to_parameters(args_schema: Any) -> Dict[str, Any]:
    """Best-effort parameters block from a LangChain tool's Pydantic args_model."""
    if args_schema is None:
        return {"type": "object", "properties": {}}
    if hasattr(args_schema, "model_json_schema"):
        schema = args_schema.model_json_schema()
    else:
        schema = args_schema.schema()

    props = schema.get("properties") or {}
    cleaned_props: Dict[str, Any] = {}
    for key, info in props.items():
        if key in ("state", "tool_call_id", "config"):
            continue
        cleaned_props[key] = info

    required = [r for r in schema.get("required") or [] if r not in ("state", "tool_call_id", "config")]
    return {"type": "object", "properties": cleaned_props, "required": required}


def parameters_for_llm_from_tool(tool: Any) -> Dict[str, Any]:
    """Parameters JSON Schema actually sent to the model for a bound LangChain tool."""
    preserved = getattr(tool, CHAD_PARAMETERS_SCHEMA_ATTR, None)
    if preserved:
        return preserved
    if hasattr(tool, "args_schema"):
        return pydantic_args_to_parameters(tool.args_schema)
    return {"type": "object", "properties": {}}


def attach_parameters_schema(lc_tool: Any, parameters: Dict[str, Any]) -> Any:
    """Pin the canonical get_schema() parameters onto a LangChain StructuredTool."""
    try:
        object.__setattr__(lc_tool, CHAD_PARAMETERS_SCHEMA_ATTR, parameters)
    except (TypeError, AttributeError):
        setattr(lc_tool, CHAD_PARAMETERS_SCHEMA_ATTR, parameters)
    return lc_tool


class ToolSchemaBuilder:
    """Builds OpenAI-/LangChain-compatible schemas from registered tools."""

    def __init__(self, registry: Any):
        # Back-reference to the registry for tool storage + execution dispatch.
        self._registry = registry

    @property
    def _tools(self) -> Dict[str, Any]:
        return self._registry._tools

    @property
    def _categories(self) -> Dict[str, List[str]]:
        return self._registry._categories

    @property
    def _tool_sources(self) -> Dict[str, str]:
        return self._registry._tool_sources

    def extract_schema(self, name: str, tool: Any) -> Dict[str, Any]:
        """Convert a tool instance or metadata into an OpenAI-compatible schema."""
        schema = {}
        # 1. Native tools with get_schema method
        if hasattr(tool, 'get_schema'):
            schema = tool.get_schema()

        elif isinstance(tool, dict):
            # 2. Direct schema key (pre-formatted)
            if 'schema' in tool:
                schema = tool['schema']

            # 3. MCP tool format (inputSchema -> parameters)
            elif 'inputSchema' in tool:
                schema = {
                    "name": tool.get('name', name),
                    "description": tool.get('description', ''),
                    "parameters": tool.get('inputSchema', {})
                }

            # 4. Generic metadata dict
            else:
                schema = {
                    "name": tool.get('name', name),
                    "description": tool.get('description', ''),
                    "parameters": tool.get('parameters', {})
                }

        # 5. Fallback for objects with description attribute
        else:
            schema = {
                "name": name,
                "description": getattr(tool, 'description', '')
            }

        # 6. INJECT TOOL-SPECIFIC CONFIG (PRESETS/RULES)
        # We append preferred settings/context saved via manage_tool_configs
        config_context = ToolConfigManager.get_config_for_tool(name)
        if config_context:
            logger.debug(f"[ REGISTRY ] Injecting config for '{name}': {config_context}")
            current_desc = schema.get('description', '')
            schema['description'] = f"{current_desc}\n\n[ PREFERRED CONFIG ]: {config_context}"

        # 7. APPLY SYSTEM-WIDE COGNITIVE HARDENING OVERRIDES
        # Prevents model grammar engines from defaulting optional numbers to zero, etc.
        schema = self.apply_hardening_overrides(name, schema)

        return schema

    def apply_hardening_overrides(self, name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dynamically patch problematic tool schemas before exposing them to models.
        Solves known brittle MCP constraints by modifying JSON schema required arrays
        and injecting safety instructions directly into parameter descriptions.
        """
        try:
            params = schema.get("parameters", {})
            props = params.get("properties", {})

            # --- BRAVE SEARCH HARDENING ---
            # Local grammar engines and protobuf layers routinely default 'count' to 0.0 if left optional.
            # Brave API immediately rejects 0. Promoting it to 'Required' forces conscious selection.
            if name in ["brave_web_search", "brave_local_search"]:
                if "count" in props:
                    props["count"]["description"] = "CRITICAL: Number of results (Must be an integer from 1 to 20). DO NOT pass 0."
                    # Force promotion to Required
                    req_list = params.get("required", [])
                    if not isinstance(req_list, list):
                        req_list = []
                    if "count" not in req_list:
                        req_list.append("count")
                        params["required"] = req_list
                        logger.debug(f"[ HARDENING ] Promoted 'count' to REQUIRED for {name} to block Zero-Default bugs.")

            # Re-attach safely
            if props:
                params["properties"] = props
                schema["parameters"] = params

        except Exception as e:
            logger.warning(f"[ HARDENING ] Failed to apply override for {name}: {e}")

        return schema

    def list_catalog_entries(self, *, max_description_len: int = 300) -> List[Dict[str, Any]]:
        """UI / routine-builder friendly tool list with parameter schemas."""
        skip_meta = {
            "search_for_tool", "ask_clarification", "call_mcp_tool",
            "suggest_mcp_install", "escalate_to_agent",
        }
        entries: List[Dict[str, Any]] = []
        for name in sorted(self._tools.keys()):
            if name in skip_meta:
                continue
            tool = self._tools[name]
            try:
                schema = self.extract_schema(name, tool)
            except Exception:
                continue
            category = next(
                (cat for cat, names in self._categories.items() if name in names),
                "custom",
            )
            desc = (schema.get("description") or "")[:max_description_len]
            source = self._tool_sources.get(name, "local")
            mcp_server = tool.get("mcp_server") if isinstance(tool, dict) else None
            entries.append({
                "name": name,
                "description": desc,
                "category": category,
                "parameters": schema.get("parameters") or {},
                "source": source,
                "server_name": getattr(mcp_server, "name", None) if mcp_server else None,
            })
        return entries

    def get_schemas_for_categories(self, categories: List[str]) -> List[Dict[str, Any]]:
        """Get tool schemas for a list of categories."""
        schemas = []
        for category in categories:
            tool_names = self._categories.get(category, [])
            for name in tool_names:
                tool = self._tools.get(name)
                if not tool:
                    continue
                schemas.append(self.extract_schema(name, tool))
        return schemas

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """Return all registered tool schemas."""
        schemas = []
        for name, tool in self._tools.items():
            schemas.append(self.extract_schema(name, tool))
        return schemas

    def get_tools_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        """
        Retrieves a subset of tools for a specific specialized agent domain.
        Returns a list of LangChain-compatible tool objects (schemas).
        """
        # 1. Define the domain mapping to categories and specific tool names
        domain_map = {
            "workspace": {
                "categories": ["workspace", "canvas", "google-drive", "brave-search", "fetch", "todoist"],
                "tools": [
                    "calendar_list_events",
                    "google_drive_search",
                    "google_drive_list_folder",
                    "google_drive_read_file",
                    "google_drive_create_doc",
                    "google_drive_edit_doc",
                    "call_mcp_tool",
                ]
            },
            "ui_automation": {
                # "ui_automation" was a dead category (no tool ever declared it) and the old
                # "tools" list named placeholder/legacy MCP tool names (Snapshot/Screenshot/
                # Click/Type/window_management) that don't match this server's real tool names
                # and were unconditionally excluded anyway (MCP tools skip the "tools" list
                # below unless mcp_allow_categories permits them) — ui_automation_agent was
                # silently capped at just `open_app`. Fixed by allow-listing the real MCP
                # category (see mcp_servers.windows-semantic-ui in config.yaml) the same way
                # "browser" allow-lists "playwright" below.
                "categories": ["windows-semantic-ui"],
                "tools": ["open_app"],
                "mcp_allow_categories": ["windows-semantic-ui"],
            },
            "browser": {
                # NOTE: deliberately excludes the "browser" category (native `open_browser`,
                # which calls webbrowser.open() -> the user's REAL system-default browser).
                # browser_agent must only ever touch the sandboxed Playwright-controlled
                # profile (config.yaml mcp_servers.playwright --user-data-dir ChadBrowser);
                # binding open_browser let the model launch the user's own Chrome instead
                # (issue #287). web_search lets the agent look up the right URL first;
                # the Playwright MCP tools do all the actual driving.
                "categories": ["playwright"],
                "tools": ["web_search"],
                # DOM-first browser automation runs on the Playwright MCP tools
                # (browser_navigate/snapshot/click/type/...). MCP tools are excluded
                # from subagents by default (discover-not-execute), but the browser_agent
                # is the one specialist whose entire job IS to execute them — so the
                # 'playwright' category is allow-listed here. See issue #275 / epic #274.
                "mcp_allow_categories": ["playwright"],
            },
            "web_research": {
                # "web" surfaces the consolidated web_search tool (other web/* tools below).
                # brave-search/fetch are MCP categories — excluded from subagents at execution
                # time, but web_search dispatches them internally via the registry.
                "categories": ["web", "brave-search", "fetch"],
                "tools": [
                    "web_search",
                    "youtube_toolkit",
                    "search_browser_history",
                    "refresh_browser_history",
                ]
            },
            "code": {
                "categories": ["coding", "github", "supabase-mcp-server", "supabase"],
                "tools": [
                    "run_sandboxed_python", "run_sandboxed_bash", "install_package",
                    "wait",
                    "build_html_file", "write_file", "propose_file_plan",
                    "create_directory", "find_project", "execute_approved_plan",
                    "render_animation",
                ],
            },
            "system": {
                "categories": ["os", "system", "filesystem"],
                "tools": ["list_directory", "read_file", "write_file", "get_system_info"]
            },
            "memory": {
                "categories": ["memory"],
                "tools": ["manage_user_memory", "manage_persona_rules", "search_browser_history", "refresh_browser_history"]
            },
            "traversal": {
                "categories": [],
                "tools": [
                    "google_drive_search",
                    "google_drive_list_folder",
                    "google_drive_read_file",
                    "fuzzy_find_local",
                    "list_directory",
                    "read_file",
                    "search_for_tool",
                    "refresh_google_auth",
                ]
            },
            "canvas": {
                "categories": ["canvas"],
                "tools": [
                    "read_file",
                    "write_file",
                    "list_directory",
                ]
            }
        }

        config = domain_map.get(domain)
        if not config:
            logger.warning(f"[ REGISTRY ] Unknown domain requested: {domain}")
            return []

        schemas = []
        seen_names = set()

        # Categories whose MCP tools this domain is explicitly allowed to EXECUTE
        # (normally MCP tools are discover-only for subagents). The browser_agent
        # needs the Playwright MCP tools to drive the DOM-first loop.
        mcp_allow_categories = set(config.get("mcp_allow_categories", []))

        # Add tools from categories
        for category in config.get("categories", []):
            tool_names = self._categories.get(category, [])
            for name in tool_names:
                if name in seen_names:
                    continue
                # EXCLUDE MCP TOOLS FROM SUBAGENTS: they can discover, but not execute —
                # UNLESS the domain allow-lists this category (e.g. browser -> playwright).
                if self._tool_sources.get(name) == 'mcp' and category not in mcp_allow_categories:
                    continue
                tool = self._tools.get(name)
                if tool:
                    schemas.append(self.extract_schema(name, tool))
                    seen_names.add(name)

        # Add specific tools (fallback/override)
        for name in config.get("tools", []):
            if name in seen_names:
                continue
            # EXCLUDE MCP TOOLS FROM SUBAGENTS: they can discover, but not execute —
            # UNLESS the domain allow-lists the category this tool actually belongs to.
            # (Mirrors the same check in the categories loop above; this loop used to skip
            # MCP tools unconditionally with no allow-list escape hatch at all.)
            if self._tool_sources.get(name) == 'mcp' and not any(
                name in self._categories.get(cat, []) for cat in mcp_allow_categories
            ):
                continue
            tool = self._tools.get(name)
            if tool:
                schemas.append(self.extract_schema(name, tool))
                seen_names.add(name)

        logger.info(f"[ REGISTRY ] Domain '{domain}' loaded with {len(schemas)} tools.")
        return schemas

    # ------------------------------------------------------------------ #
    # LangChain wrapping
    # ------------------------------------------------------------------ #

    def get_langchain_tool_by_name(self, name: str) -> Optional[Any]:
        """Retrieve a specific tool by name, wrapped as a LangChain StructuredTool."""
        tool = self._tools.get(name)
        if not tool:
            return None
        schema = self.extract_schema(name, tool)
        return self.wrap_lc_tool(name, schema)

    def get_langchain_tools_by_domain(self, domain: str) -> List[Any]:
        """Retrieve a subset of tools for a domain, wrapped as LangChain StructuredTools."""
        schemas = self.get_tools_by_domain(domain)
        return [self.wrap_lc_tool(schema['name'], schema) for schema in schemas]

    def get_all_langchain_tools(self) -> List[Any]:
        """Return all registered tools wrapped as LangChain StructuredTools."""
        lc_tools = []
        for name, tool in self._tools.items():
            schema = self.extract_schema(name, tool)
            lc_tools.append(self.wrap_lc_tool(name, schema))
        return lc_tools

    def wrap_lc_tool(self, name: str, schema: Dict[str, Any]) -> Any:
        """Convert a tool schema into a LangChain StructuredTool."""
        from langchain_core.tools import StructuredTool
        from pydantic import create_model, Field

        tool = self._tools.get(name)
        description = schema.get('description', '')
        params = schema.get('parameters', {}).get('properties', {})
        required = schema.get('parameters', {}).get('required', [])

        # Create a dynamic Pydantic model for the tool arguments
        fields = {}
        for p_name, p_info in params.items():
            p_type = p_info.get('type', 'string')
            p_desc = p_info.get('description', '')

            # Basic type mapping
            if isinstance(p_type, list):
                from typing import Union, Any
                type_map = {
                    'integer': int,
                    'boolean': bool,
                    'number': float,
                    'array': list,
                    'object': dict,
                    'string': str,
                }
                matched_types = [type_map[t] for t in p_type if t in type_map]
                py_type = Union[tuple(matched_types)] if matched_types else Any
            else:
                py_type = str
                if p_type == 'integer':
                    py_type = int
                elif p_type == 'boolean':
                    py_type = bool
                elif p_type == 'number':
                    py_type = float
                elif p_type == 'array':
                    py_type = list
                elif p_type == 'object':
                    py_type = dict
                elif p_type == 'any':
                    from typing import Any
                    py_type = Any

            default = ... if p_name in required else p_info.get("default", None)
            fields[p_name] = (py_type, Field(default=default, description=p_desc))

        args_model = create_model(f"{name}_args", **fields)

        # ARCHITECTURE NOTE: We wrap the tool execution in a notification layer.
        # This is critical for LangGraph integration because LangGraph's ToolNode
        # calls tools directly, bypassing our high-level coordinators.
        # This wrapper ensures the UI (ChatStream) always sees tool starts/ends.
        from ..event_wrapper import ToolEventWrapper
        exec_coro = ToolEventWrapper.wrap_as_langchain_tool(
            tool_name=name,
            original_executor=self._registry.execute_tool,
            event_bus=self._registry._event_bus,
            tool_instance=tool
        )

        parameters = schema.get("parameters") or {"type": "object", "properties": {}}

        lc_tool = StructuredTool.from_function(
            name=name,
            description=description,
            func=None,  # Async only
            coroutine=exec_coro,
            args_schema=args_model,
        )
        return attach_parameters_schema(lc_tool, parameters)
