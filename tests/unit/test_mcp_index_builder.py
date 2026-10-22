import sys
import json
import pytest
import asyncio
import shutil
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tools.system.mcp.mcp_index_builder import MCPIndexBuilder
from src.infrastructure.tools import ToolRegistry
from src.core.tools.mcp_registry import MCPRegistry


class TestMCPIndexBuilder:

    @pytest.fixture
    def temp_mcp_root(self, tmp_path):
        root_dir = tmp_path / "mcp"
        yield root_dir
        if root_dir.exists():
            shutil.rmtree(root_dir)

    @pytest.mark.asyncio
    async def test_build_all_with_registry_tools(self, temp_mcp_root):
        # Mock MCP registry
        mcp_registry = MagicMock(spec=MCPRegistry)
        mock_server = MagicMock()
        mock_server.name = "test-server"
        mock_server.config = {"description": "A test server description"}
        mcp_registry.get_servers.return_value = {"test-server": mock_server}

        # Mock ToolRegistry tools
        tool_registry = MagicMock(spec=ToolRegistry)
        mock_tool_metadata = {
            "source": "mcp",
            "mcp_server": mock_server,
            "name": "test_tool",
            "description": "This is a test tool"
        }
        tool_registry._tools = {
            "test_tool": mock_tool_metadata,
            "another_local_tool": {"source": "local", "name": "local_tool"} # should be ignored
        }
        
        # Mock tool registry schema extraction
        tool_registry._extract_schema.return_value = {
            "name": "test_tool",
            "description": "This is a test tool description\nExtended instructions.",
            "parameters": {"type": "object", "properties": {}}
        }

        builder = MCPIndexBuilder(
            mcp_registry=mcp_registry,
            tool_registry=tool_registry,
            mcp_root=str(temp_mcp_root)
        )
        
        await builder.build_all()

        # Assert folders were created
        server_dir = temp_mcp_root / "test-server"
        assert server_dir.exists()
        assert server_dir.is_dir()

        # Assert index.json was created
        index_file = server_dir / "index.json"
        assert index_file.exists()
        with open(index_file, "r") as f:
            index_data = json.load(f)
            assert index_data["server"] == "test-server"
            assert index_data["description"] == "A test server description"
            assert len(index_data["tools"]) == 1
            assert index_data["tools"][0]["name"] == "test_tool"
            assert index_data["tools"][0]["description"] == "This is a test tool description"

        # Assert tool schema file was created
        tool_file = server_dir / "test_tool.json"
        assert tool_file.exists()
        with open(tool_file, "r") as f:
            tool_data = json.load(f)
            assert tool_data["name"] == "test_tool"
            assert "parameters" in tool_data

    @pytest.mark.asyncio
    async def test_build_all_fallback_to_server_list_tools(self, temp_mcp_root):
        # Mock MCP registry and server
        mcp_registry = MagicMock(spec=MCPRegistry)
        mock_server = MagicMock()
        mock_server.name = "fallback-server"
        mock_server.config = {}
        mock_server.list_tools = AsyncMock(return_value=[
            {"name": "direct_tool", "description": "Fetched directly from server"}
        ])
        mcp_registry.get_servers.return_value = {"fallback-server": mock_server}

        # ToolRegistry has NO tools registered for this server
        tool_registry = MagicMock(spec=ToolRegistry)
        tool_registry._tools = {}
        tool_registry._extract_schema.side_effect = lambda name, data: {
            "name": name,
            "description": data.get("description", ""),
            "parameters": {}
        }

        builder = MCPIndexBuilder(
            mcp_registry=mcp_registry,
            tool_registry=tool_registry,
            mcp_root=str(temp_mcp_root)
        )
        
        await builder.build_all()

        # Assert server direct list_tools was called
        mock_server.list_tools.assert_called_once()

        # Assert index.json was created with direct tools
        server_dir = temp_mcp_root / "fallback-server"
        index_file = server_dir / "index.json"
        assert index_file.exists()
        with open(index_file, "r") as f:
            index_data = json.load(f)
            assert len(index_data["tools"]) == 1
            assert index_data["tools"][0]["name"] == "direct_tool"
