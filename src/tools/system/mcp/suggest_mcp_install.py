"""
SuggestMCPInstallTool — lets Chad guide the user through installing an MCP server
mid-conversation by opening the settings install dialog directly in the UI.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("SuggestMCPInstallTool")

REGISTRY_BASE = "https://registry.modelcontextprotocol.io/v0"

# Short-name aliases for common servers so Chad can say "brave-search" instead of
# the full reverse-DNS registry ID.
KNOWN_SERVERS = {
    "brave-search": {"id": "io.modelcontextprotocol/server-brave-search", "title": "Brave Search"},
    "github": {"id": "io.modelcontextprotocol/server-github", "title": "GitHub"},
    "filesystem": {"id": "io.modelcontextprotocol/server-filesystem", "title": "Filesystem"},
    "fetch": {"id": "io.modelcontextprotocol/mcp-fetch-server", "title": "Fetch"},
    "google-maps": {"id": "io.modelcontextprotocol/server-google-maps", "title": "Google Maps"},
    "postgres": {"id": "io.modelcontextprotocol/server-postgres", "title": "PostgreSQL"},
    "sqlite": {"id": "io.modelcontextprotocol/server-sqlite", "title": "SQLite"},
    "todoist": {"id": "com.ganiev/todoist-mcp-server", "title": "Todoist"},
    "notion": {"id": "com.suekou/mcp-notion-server", "title": "Notion"},
    "playwright": {"id": "com.microsoft/playwright-mcp", "title": "Playwright"},
    "supabase": {"id": "com.supabase/mcp", "title": "Supabase"},
}


class SuggestMCPInstallTool:
    """
    Tool that opens the MCP install dialog for a specific server in the settings UI.

    Chad calls this when it detects a capability gap (e.g. user asks to search
    the web but Brave Search isn't installed). The UI opens the install flow;
    Chad doesn't block waiting for the result.
    """

    name = "suggest_mcp_install"
    description = (
        "Open the MCP server install dialog for the user when you need a capability "
        "that isn't currently configured. Use this when the user asks for something "
        "you could do with an MCP server but that server isn't installed. "
        "Example: user asks to search the web → suggest brave-search. "
        "Supported short names: brave-search, github, filesystem, fetch, google-maps, "
        "postgres, sqlite, todoist, notion, playwright, supabase."
    )
    category = "system"

    def __init__(self, event_bus=None):
        self._event_bus = event_bus

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id": {
                        "type": "string",
                        "description": (
                            "Short name (e.g. 'brave-search') or full registry ID "
                            "(e.g. 'io.modelcontextprotocol/server-brave-search')."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why this server is needed.",
                    },
                },
                "required": ["server_id"],
            },
        }

    async def execute(self, server_id: str, reason: Optional[str] = None, context=None, **kwargs) -> Dict[str, Any]:
        from src.application.events import MCPInstallRequested

        # Resolve short name → registry ID + title
        known = KNOWN_SERVERS.get(server_id.lower())
        registry_id = known["id"] if known else server_id
        title = known["title"] if known else server_id

        if not self._event_bus:
            return {
                "success": False,
                "message": "Event bus not available — cannot open install dialog.",
            }

        try:
            self._event_bus.publish(MCPInstallRequested(
                server_id=registry_id,
                title=title,
                reason=reason,
            ))
            logger.info(f"[ MCP ] MCPInstallRequested published for '{registry_id}'")
            return {
                "success": True,
                "server_id": registry_id,
                "title": title,
                "message": (
                    f"I've opened the install dialog for **{title}** in your settings. "
                    f"Follow the prompts to complete setup — you may need to provide an API key."
                ),
            }
        except Exception as e:
            logger.error(f"SuggestMCPInstallTool failed: {e}", exc_info=True)
            return {"success": False, "message": str(e)}
