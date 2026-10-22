"""
MCP Scanner - Discovers tools from MCP servers

Queries MCP servers for their tool catalogs and registers them.
"""

import logging
from typing import Dict, Any
from src.shared.logger import setup_logger

logger = setup_logger("MCPScanner")


class MCPScanner:
    """
    Scans MCP servers for available tools.
    """

    def __init__(self, registry):
        """
        Initialize scanner.

        Args:
            registry: ToolRegistry instance to register MCP tools into
        """
        self.registry = registry

    async def scan_server(self, server: Any, category: str = 'mcp') -> None:
        """
        Scan an MCP server for tools.

        Args:
            server: MCPServerEntry instance
            category: Category for these tools (default: 'mcp')
        """
        try:
            # Get tools from server
            tools = await server.list_tools()

            if not tools:
                logger.warning(f"[ MCP ] Server '{server.name}' returned 0 tools — server may have failed to start.")
            else:
                logger.warning(f"[ MCP ] Server '{server.name}' registered {len(tools)} tools.")

            # Register each tool
            excluded = server.config.get('excluded_tools', [])
            for tool_metadata in tools:
                tool_name = tool_metadata.get('name')
                if tool_name in excluded:
                    logger.debug(f"Skipping excluded MCP tool '{tool_name}' from '{server.name}'")
                    continue
                    
                self.registry.register_mcp_tool(
                    tool_metadata=tool_metadata,
                    server=server,
                    category=category
                )

        except Exception as e:
            logger.error(f"Failed to scan MCP server '{server.name}': {e}")
