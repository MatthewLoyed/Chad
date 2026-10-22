import sys
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.tools import ToolRegistry
from src.core.tools.mcp_registry import MCPRegistry, MCPServerEntry


class TestMCPFilesystemE2E:

    @pytest.mark.asyncio
    async def test_mcp_filesystem_discovery_and_execution_flow(self):
        # 1. Setup registries
        tool_registry = ToolRegistry()
        mcp_registry = MCPRegistry()
        
        # 2. Register mock MCP server and tools
        mock_server = MagicMock(spec=MCPServerEntry)
        mock_server.name = "test-mcp-server"
        mock_server.config = {"description": "Mocked MCP server description"}
        
        # list_tools returns some tools
        mock_tools_def = [
            {
                "name": "mcp_tool_one",
                "description": "MCP Tool Description",
                "inputSchema": {"type": "object", "properties": {}}
            }
        ]
        mock_server.list_tools = AsyncMock(return_value=mock_tools_def)
        
        # Register server to MCP registry
        mcp_registry._servers["test-mcp-server"] = mock_server
        
        # 3. Simulate system warmup: scanning MCP server and registering tools
        await tool_registry.discover_mcp_servers(mcp_registry)
        
        # ToolRegistry should now contain the MCP tool
        assert "mcp_tool_one" in tool_registry._tools
        assert tool_registry._tool_sources.get("mcp_tool_one") == "mcp"
        
        # 4. Verify turn-start schema selection excludes the MCP tool
        turn_result = tool_registry.get_schemas_for_turn(query="run mcp tool")
        schemas = turn_result["schemas"]
        schema_names = [s["name"] for s in schemas]
        
        # Core tools should be present
        assert "call_mcp_tool" in schema_names
        
        # BUT the raw MCP tool itself must be excluded!
        assert "mcp_tool_one" not in schema_names
        
        # 5. Verify call_mcp_tool execution routes to the MCP server
        # Inject registry link
        tool_registry.mcp_registry = mcp_registry
        
        mock_server.call_tool = AsyncMock(return_value=[MagicMock(text="Tool output", model_dump=lambda: {"type": "text", "text": "Tool output"})])
        
        # Execute via the tool registry execute interface
        exec_result = await tool_registry.execute_tool(
            tool_name="call_mcp_tool",
            parameters={
                "server_name": "test-mcp-server",
                "tool_name": "mcp_tool_one",
                "arguments": {"param": "val"}
            }
        )
        
        assert exec_result["success"] is True
        assert exec_result["result"][0]["text"] == "Tool output"
        
        # Verify the server was actually called
        mock_server.call_tool.assert_called_once_with("mcp_tool_one", {"param": "val"})
