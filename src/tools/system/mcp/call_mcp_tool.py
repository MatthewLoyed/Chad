"""
Call MCP Tool - Executes a tool on a registered MCP server.
"""

from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("CallMCPTool")


class CallMCPTool(BaseTool):
    """
    Executes a tool on a specified MCP server.
    Used for on-demand dynamic execution.
    """

    name = "call_mcp_tool"
    description = (
        "Execute a tool on an MCP server. "
        "Before calling this tool, you MUST discover the available servers and their schemas on disk. "
        "Use list_directory on the 'mcp/' folder to discover servers, then read_file on '<server>/index.json' "
        "to discover tool names, and optionally read_file on '<server>/<tool_name>.json' for parameter details."
    )
    category = "system"
    is_side_effecting = True  # Safely assume MCP calls can have side effects

    def __init__(self, registry: Any, config: Optional[Dict[str, Any]] = None):
        """
        Initialize CallMCPTool.

        Args:
            registry: ToolRegistry instance
            config: Optional configuration
        """
        super().__init__(config)
        self.registry = registry

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "Name of the MCP server (e.g. 'github', 'playwright', 'brave-search')."
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool to execute on the server (e.g. 'list_issues', 'click')."
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Key-value arguments matching the schema of the tool to be called."
                    }
                },
                "required": ["server_name", "tool_name", "arguments"]
            }
        }

    async def execute(self, server_name: str, tool_name: str, arguments: Dict[str, Any] = None, **kwargs) -> Dict[str, Any]:
        """
        Execute the tool on the specified MCP server.

        Reuses the existing MCPExecutor to execute the tool to benefit from parameter
        coercion, snapshot pruning, focus checks, error hints, and terminal actions.
        """
        if server_name is not None:
            server_name = str(server_name).strip().strip("\"'").strip()
        if tool_name is not None:
            tool_name = str(tool_name).strip().strip("\"'").strip()

        # Handle nested or string arguments from various LLM quirks
        if arguments is None:
            arguments = {}
        elif isinstance(arguments, str):
            try:
                import json
                arguments = json.loads(arguments)
            except Exception:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        if "arguments" in arguments and isinstance(arguments["arguments"], dict) and len(arguments) <= 3:
            arguments = arguments["arguments"]

        if "server_name" in arguments and arguments.get("server_name") == server_name:
            arguments = {k: v for k, v in arguments.items() if k != "server_name"}
        if "tool_name" in arguments and arguments.get("tool_name") == tool_name:
            arguments = {k: v for k, v in arguments.items() if k != "tool_name"}

        if server_name == "canva":
            if tool_name == "generate-design":
                if "query" not in arguments or not arguments["query"]:
                    if arguments.get("prompt"):
                        arguments["query"] = arguments["prompt"]
                    elif arguments.get("brief"):
                        arguments["query"] = arguments["brief"]

                valid_design_types = {
                    "business_card", "card", "desktop_wallpaper", "doc", "document", "email",
                    "facebook_cover", "facebook_post", "flyer", "infographic", "instagram_post",
                    "invitation", "logo", "phone_wallpaper", "photo_collage", "pinterest_pin",
                    "postcard", "poster", "presentation", "proposal", "report", "resume",
                    "twitter_post", "your_story", "youtube_banner", "youtube_thumbnail",
                }
                if "design_type" not in arguments or not arguments["design_type"]:
                    for candidate_key in ("dimensions", "format", "type", "designType", "category"):
                        val = str(arguments.get(candidate_key, "")).lower().strip()
                        if val in valid_design_types:
                            arguments["design_type"] = val
                            break
                    if "design_type" not in arguments or not arguments["design_type"]:
                        text_corpus = f"{arguments.get('query', '')} {arguments.get('prompt', '')}".lower()
                        for dt in ("poster", "flyer", "instagram_post", "presentation", "logo", "infographic", "invitation", "business_card", "postcard", "resume", "doc"):
                            if dt in text_corpus:
                                arguments["design_type"] = dt
                                break
                        else:
                            arguments["design_type"] = "poster"
            elif tool_name == "create-design":
                if "brief" not in arguments or not arguments["brief"]:
                    if arguments.get("query"):
                        arguments["brief"] = arguments["query"]
                    elif arguments.get("prompt"):
                        arguments["brief"] = arguments["prompt"]

        logger.info(f"Executing call_mcp_tool: server='{server_name}', tool='{tool_name}'")

        mcp_registry = getattr(self.registry, "mcp_registry", None)
        if not mcp_registry:
            error_msg = "MCP registry is not initialized in ToolRegistry."
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        server_entry = mcp_registry.get(server_name)
        if not server_entry:
            # Self-healing fallback: Check if the model mistakenly passed a native tool
            target_native_tool = None
            registry_tools = getattr(self.registry, "_tools", None)
            if registry_tools is None:
                registry_tools = getattr(self.registry, "tools", None)
            if isinstance(registry_tools, dict):
                tool_sources = getattr(self.registry, "_tool_sources", {})
                if tool_name in registry_tools and tool_sources.get(tool_name) != "mcp":
                    target_native_tool = tool_name
                elif server_name in registry_tools and tool_sources.get(server_name) != "mcp":
                    target_native_tool = server_name

            if target_native_tool and hasattr(self.registry, "execute_tool"):
                logger.warning(
                    f"call_mcp_tool called with server_name='{server_name}', tool_name='{tool_name}'. "
                    f"'{target_native_tool}' is a native Chad tool, not an MCP server. "
                    f"Redirecting execution to native tool registry."
                )
                try:
                    res = await self.registry.execute_tool(target_native_tool, arguments)
                    if isinstance(res, dict):
                        res["_redirect_note"] = (
                            f"'{target_native_tool}' is a native Chad tool, not an MCP server. "
                            f"It was executed directly. In future turns, call `{target_native_tool}` directly without `call_mcp_tool`."
                        )
                    return res
                except Exception as e:
                    error_msg = f"Failed to execute native tool '{target_native_tool}': {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    return {"success": False, "error": error_msg}

            error_msg = self._not_found_message(server_name)
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        # Create a mock tool representation that matches what MCPExecutor can handle
        mock_tool = {
            'name': tool_name,
            'mcp_server': server_entry,
            'source': 'mcp'
        }

        try:
            # Delegate tool execution to standard MCPExecutor strategy
            from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
            executor = MCPExecutor()
            
            result = await executor.execute(mock_tool, arguments)
            return result
        except Exception as e:
            error_msg = f"Failed to execute MCP tool '{tool_name}' on server '{server_name}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {"success": False, "error": error_msg}

    def _not_found_message(self, server_name: str) -> str:
        """Distinguish "this server doesn't exist" from "this is a real Chad connector that
        just isn't connected" — the two look identical from mcp_registry.get() (it's only
        populated with servers that passed get_effective_mcp_servers(), i.e. enabled AND
        credentialed), but the model needs different next steps for each."""
        from src.infrastructure.connectors.policy import SHIPPED_MCP_SERVER_IDS

        if server_name not in SHIPPED_MCP_SERVER_IDS:
            return f"MCP server '{server_name}' not found. Check list_directory on the mcp/ folder."

        return (
            f"MCP server '{server_name}' is a known Chad connector but isn't currently connected "
            f"(missing credentials or disabled). You can call `manage_connectors(action='get_schema', connector_id='{server_name}')` "
            f"to inspect required inputs, ask the user for them, and call `manage_connectors(action='configure', connector_id='{server_name}', inputs={{...}})` to enable it."
            + self._connector_hint(server_name)
        )

    def _connector_hint(self, server_name: str) -> str:
        if server_name == "google-drive":
            return (
                " For Google Drive, use the native `google_drive_search` / "
                "`google_drive_list_folder` / `google_drive_read_file` tools instead — they "
                "don't depend on this MCP server. If those fail with an authentication error, "
                "call `refresh_google_auth` to re-authenticate, then retry."
            )
        return ""

    def format_action(self, params: Dict[str, Any]) -> str:
        """
        Format a human-friendly message for call_mcp_tool.
        """
        from src.core.tools.formatter import ToolFormatter
        server_name = params.get("server_name") or params.get("ServerName")
        target_tool = params.get("tool_name") or params.get("ToolName")
        arguments = params.get("arguments") or params.get("Arguments") or {}
        if server_name and target_tool:
            return ToolFormatter._format_mcp_display(server_name, target_tool, arguments)
        return f"Executing Call Mcp Tool..."
