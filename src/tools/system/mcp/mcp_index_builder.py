"""
MCP Index Builder - Boot-time tool index generator for filesystem-first discovery.
Generates index.json and <tool_name>.json files inside the project-local mcp/ directory.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.shared.logger import setup_logger

logger = setup_logger("MCPIndexBuilder")


class MCPIndexBuilder:
    """
    Builds lightweight index files and full tool schemas on disk for MCP servers.
    This enables on-demand filesystem-first discovery.
    """

    def __init__(self, mcp_registry: Any, tool_registry: Any, mcp_root: str):
        """
        Initialize builder.

        Args:
            mcp_registry: MCPRegistry instance
            tool_registry: ToolRegistry instance
            mcp_root: Directory path where MCP schemas will be written
        """
        self.mcp_registry = mcp_registry
        self.tool_registry = tool_registry
        self.mcp_root = Path(mcp_root)

    async def build_all(self) -> None:
        """
        Build index files for all registered and connected MCP servers.
        """
        try:
            # Ensure target root directory exists
            self.mcp_root.mkdir(parents=True, exist_ok=True)
            logger.info(f"Building MCP index files at: {self.mcp_root}")

            # Get servers list
            servers = self.mcp_registry.get_servers()
            for server_name, server in servers.items():
                await self.build_server_index(server_name, server)

            logger.info("MCP index building complete.")
        except Exception as e:
            logger.error(f"Failed to build MCP index: {e}", exc_info=True)

    async def build_server_index(self, server_name: str, server: Any) -> None:
        """
        Build the index.json and tool schemas for a single server.
        """
        try:
            server_dir = self.mcp_root / server_name
            server_dir.mkdir(parents=True, exist_ok=True)

            # Get tool definitions from registry (already scanned and registered)
            server_tools = []
            mcp_tools = self.tool_registry._tools

            # Filter tools belonging to this server
            for tool_name, tool_data in mcp_tools.items():
                if isinstance(tool_data, dict) and tool_data.get('source') == 'mcp':
                    mcp_server = tool_data.get('mcp_server')
                    if mcp_server and mcp_server.name == server_name:
                        server_tools.append((tool_name, tool_data))

            # If no tools in registry, try querying server directly (fallback)
            if not server_tools:
                logger.debug(f"No tools registered for '{server_name}' in ToolRegistry. Fetching directly.")
                try:
                    tools_metadata = await server.list_tools()
                    for meta in tools_metadata:
                        tool_name = meta.get('name')
                        server_tools.append((tool_name, meta))
                except Exception as list_err:
                    logger.warning(f"Could not list tools directly from server '{server_name}': {list_err}")

            # Build index list and write individual schema files
            index_tools = []
            excluded = server.config.get('excluded_tools', [])

            for tool_name, tool_data in server_tools:
                if tool_name in excluded:
                    continue

                # 1. Extract schema (sanitized and hardened) via ToolRegistry
                schema = self.tool_registry._extract_schema(tool_name, tool_data)

                # 2. Write full parameter schema file
                schema_path = server_dir / f"{tool_name}.json"
                with open(schema_path, "w", encoding="utf-8") as f:
                    json.dump(schema, f, indent=2)

                # 3. Add to index list (lightweight)
                index_tools.append({
                    "name": tool_name,
                    "description": schema.get("description", "").split("\n")[0]  # Just the first line of description
                })

            # 4. Write index.json
            server_desc = server.config.get("description", f"MCP Server: {server_name}")
            index_data = {
                "server": server_name,
                "description": server_desc,
                "tools": index_tools
            }

            index_path = server_dir / "index.json"
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(index_data, f, indent=2)

            logger.debug(f"Generated index and {len(index_tools)} tool schemas for server '{server_name}'")

        except Exception as e:
            logger.error(f"Failed to build index for server '{server_name}': {e}", exc_info=True)
