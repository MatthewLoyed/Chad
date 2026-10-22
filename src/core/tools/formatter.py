from typing import Any, Dict, Optional, Union
import logging

logger = logging.getLogger("ToolFormatter")

def tool_display(template: Union[str, Dict[str, str]]):
    """
    Decorator to attach a human-readable display template to a tool.
    
    Example (Simple):
    @tool_display("Searching Google for '{query}'...")
    
    Example (Multi-mode):
    @tool_display({
        "launch": "Launching {name}...",
        "switch": "Switching to {name}...",
        "resize": "Resizing {name} window..."
    })
    """
    def decorator(cls):
        cls.display_template = template
        return cls
    return decorator

class ToolFormatter:
    """
    Utility to convert technical tool calls into human-friendly strings.
    """

    @staticmethod
    def _format_mcp_display(server_name: str, tool_name: str, params: Dict[str, Any]) -> str:
        """Helper to format MCP-style tool displays."""
        # Format server name: github -> GitHub, brave-search -> Brave Search
        server_display = server_name.replace("-", " ").replace("_", " ").title()
        if server_display.lower() == "github":
            server_display = "GitHub"
        elif server_display.lower() == "google-drive":
            server_display = "Google Drive"

        # Format tool name: list_issues -> List Issues
        tool_display_name = tool_name.replace("_", " ").title()

        # Format arguments
        arg_str = ""
        if params:
            # Filter out internal/empty params
            arg_items = []
            for k, v in sorted(params.items()):
                if k.startswith('_'): continue
                if v is None or v == "": continue
                
                # Truncate long values for UI display
                val_str = str(v)
                if len(val_str) > 80:
                    val_str = val_str[:77] + "..."
                
                # Use icons for common params
                key_display = k
                if k == "query": key_display = "🔍"
                elif k == "path": key_display = "📁"
                elif k in ["content", "body", "text"]: key_display = "📝"
                elif k == "url": key_display = "🌐"
                
                arg_items.append(f"{key_display}: {val_str}")
            
            if arg_items:
                arg_str = f" [{', '.join(arg_items)}]"

        return f"{server_display} ⚡ {tool_display_name}{arg_str}"

    @staticmethod
    def format(tool_name: str, params: Dict[str, Any], tool_instance: Optional[Any] = None) -> str:
        """
        Formats an action message using the tool's internal metadata.
        """
        # 1. Check for dedicated format_action method (highest precedence)
        if tool_instance and hasattr(tool_instance, 'format_action'):
            try:
                return tool_instance.format_action(params)
            except Exception as e:
                logger.debug(f"Tool {tool_name} format_action failed: {e}")

        # SPECIAL CASE: Generic MCP runner (call_mcp_tool)
        if tool_name == "call_mcp_tool":
            server_name = params.get("server_name")
            target_tool = params.get("tool_name")
            arguments = params.get("arguments", {})
            if server_name and target_tool:
                return ToolFormatter._format_mcp_display(server_name, target_tool, arguments)

        # 2. Check for display_template attribute (attached via decorator or dict key)
        template = None
        if tool_instance:
            if isinstance(tool_instance, dict):
                template = tool_instance.get('display_template')
            else:
                template = getattr(tool_instance, 'display_template', None)
        
        if template:
            try:
                # Handle multi-mode templates (dict mapping)
                if isinstance(template, dict):
                    # Use 'mode' or 'action' as the key selector
                    selector = params.get('mode') or params.get('action', 'default')
                    selected_template = template.get(selector, template.get('default'))
                    
                    if not selected_template:
                        # Fallback to generic if no specific key is found
                        selected_template = None
                    
                    if selected_template:
                        return selected_template.format(**params)
                
                # Handle simple string templates
                if isinstance(template, str):
                    return template.format(**params)
            except Exception as e:
                logger.debug(f"Template formatting failed for {tool_name}: {e}")

        # SPECIAL CASE: Eager/Discovered MCP tools (stored as dicts with source='mcp')
        if isinstance(tool_instance, dict) and tool_instance.get('source') == 'mcp':
            server = tool_instance.get('mcp_server')
            # Extract server name from the server object or the registry-level source if available
            server_name = "MCP"
            if server and hasattr(server, 'name'):
                server_name = server.name
            elif isinstance(server, str):
                server_name = server
                
            return ToolFormatter._format_mcp_display(server_name, tool_name, params)

        # 3. Fallback: Standardize snake_case to Title Case
        display_name = tool_name.replace('_', ' ').title()
        ToolFormatter._warn_missing_formatter(tool_name)
        return f"Executing {display_name}..."

    _warned_missing: set[str] = set()

    @classmethod
    def _warn_missing_formatter(cls, tool_name: str) -> None:
        if tool_name in cls._warned_missing:
            return
        cls._warned_missing.add(tool_name)
        logger.warning(
            "Tool '%s' fell back to generic display text — implement format_action() "
            "or @tool_display on the tool class (see src/infrastructure/tools/README.md)",
            tool_name,
        )
