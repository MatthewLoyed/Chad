import sys
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tools.system.mcp.call_mcp_tool import CallMCPTool
from src.infrastructure.tools import ToolRegistry


class TestCallMCPTool:

    @pytest.mark.asyncio
    async def test_schema(self):
        registry = MagicMock(spec=ToolRegistry)
        tool = CallMCPTool(registry=registry)
        schema = tool.get_schema()
        
        assert schema["name"] == "call_mcp_tool"
        assert "server_name" in schema["parameters"]["properties"]
        assert "tool_name" in schema["parameters"]["properties"]
        assert "arguments" in schema["parameters"]["properties"]
        assert "server_name" in schema["parameters"]["required"]
        assert "tool_name" in schema["parameters"]["required"]
        assert "arguments" in schema["parameters"]["required"]

    @pytest.mark.asyncio
    async def test_execute_missing_registry(self):
        # ToolRegistry has no mcp_registry attribute
        registry = object()
        tool = CallMCPTool(registry=registry)
        
        result = await tool.execute(
            server_name="test-server",
            tool_name="test-tool",
            arguments={"param": "value"}
        )
        
        assert result["success"] is False
        assert "MCP registry is not initialized" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_missing_server(self):
        registry = MagicMock()
        registry.mcp_registry = MagicMock()
        registry.mcp_registry.get.return_value = None  # Server not found
        
        tool = CallMCPTool(registry=registry)
        result = await tool.execute(
            server_name="test-server",
            tool_name="test-tool",
            arguments={"param": "value"}
        )
        
        assert result["success"] is False
        assert "MCP server 'test-server' not found" in result["error"]

    @pytest.mark.asyncio
    @patch("src.infrastructure.tools.execution.executor_strategy.MCPExecutor")
    async def test_execute_success(self, mock_executor_class):
        # Mock MCP registry and server
        mock_registry = MagicMock()
        mock_server = MagicMock()
        mock_server.name = "test-server"
        mock_registry.get.return_value = mock_server
        
        registry = MagicMock()
        registry.mcp_registry = mock_registry
        
        # Mock MCPExecutor execution
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value={"success": True, "result": "mocked-result"})
        mock_executor_class.return_value = mock_executor
        
        tool = CallMCPTool(registry=registry)
        result = await tool.execute(
            server_name="test-server",
            tool_name="test-tool",
            arguments={"param": "value"}
        )
        
        assert result["success"] is True
        assert result["result"] == "mocked-result"
        
        # Verify the mock tool shape passed to executor
        mock_executor.execute.assert_called_once_with(
            {
                'name': "test-tool",
                'mcp_server': mock_server,
                'source': 'mcp'
            },
            {"param": "value"}
        )

    @pytest.mark.asyncio
    async def test_format_action_casing_insensitivity(self):
        registry = MagicMock()
        tool = CallMCPTool(registry=registry)
        
        # Test lowercase params
        params_lower = {
            "server_name": "github",
            "tool_name": "list_issues",
            "arguments": {"query": "bug"}
        }
        res_lower = tool.format_action(params_lower)
        assert "GitHub ⚡ List Issues" in res_lower
        
        # Test capitalized params
        params_upper = {
            "ServerName": "github",
            "ToolName": "list_issues",
            "Arguments": {"query": "bug"}
        }
        res_upper = tool.format_action(params_upper)
        assert "GitHub ⚡ List Issues" in res_upper

    @pytest.mark.asyncio
    async def test_execute_self_healing_native_tool_redirect(self):
        registry = MagicMock()
        registry.mcp_registry = MagicMock()
        registry.mcp_registry.get.return_value = None  # No MCP server named 'finance'
        registry._tools = {"get_recent_transactions": MagicMock()}
        registry._tool_sources = {"get_recent_transactions": "local"}
        registry.execute_tool = AsyncMock(return_value={"success": True, "result": [{"id": 1, "amount": 42.0}]})

        tool = CallMCPTool(registry=registry)
        result = await tool.execute(
            server_name="finance",
            tool_name="get_recent_transactions",
            arguments={"days": 30}
        )

        assert result["success"] is True
        assert result["result"] == [{"id": 1, "amount": 42.0}]
        assert "_redirect_note" in result
        registry.execute_tool.assert_called_once_with("get_recent_transactions", {"days": 30})

    @pytest.mark.asyncio
    async def test_execute_self_healing_server_name_is_tool(self):
        registry = MagicMock()
        registry.mcp_registry = MagicMock()
        registry.mcp_registry.get.return_value = None
        registry._tools = {"render_chart": MagicMock()}
        registry._tool_sources = {"render_chart": "local"}
        registry.execute_tool = AsyncMock(return_value={"success": True, "chart_url": "https://quickchart.io/..."})

        tool = CallMCPTool(registry=registry)
        result = await tool.execute(
            server_name="render_chart",
            tool_name="run",
            arguments={"chart_type": "bar", "data": [10, 20]}
        )

        assert result["success"] is True
        assert result["chart_url"] == "https://quickchart.io/..."
        assert "_redirect_note" in result
        registry.execute_tool.assert_called_once_with("render_chart", {"chart_type": "bar", "data": [10, 20]})

    @pytest.mark.asyncio
    @patch("src.infrastructure.tools.execution.executor_strategy.MCPExecutor")
    async def test_execute_sanitizes_quoted_server_and_tool_names(self, mock_executor_class):
        mock_registry = MagicMock()
        mock_server = MagicMock()
        mock_server.name = "canva"
        mock_registry.get.return_value = mock_server

        registry = MagicMock()
        registry.mcp_registry = mock_registry

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value={"success": True, "result": "design-created"})
        mock_executor_class.return_value = mock_executor

        tool = CallMCPTool(registry=registry)
        result = await tool.execute(
            server_name='"canva"',
            tool_name='"create-design"',
            arguments={"server_name": "canva", "tool_name": "create-design", "arguments": {"query": "poster"}}
        )

        assert result["success"] is True
        mock_registry.get.assert_called_once_with("canva")
        mock_executor.execute.assert_called_once_with(
            {
                'name': "create-design",
                'mcp_server': mock_server,
                'source': 'mcp'
            },
            {"query": "poster", "brief": "poster"}
        )

    @pytest.mark.asyncio
    @patch("src.infrastructure.tools.execution.executor_strategy.MCPExecutor")
    async def test_execute_normalizes_canva_generate_design_arguments(self, mock_executor_class):
        mock_registry = MagicMock()
        mock_server = MagicMock()
        mock_server.name = "canva"
        mock_registry.get.return_value = mock_server

        registry = MagicMock()
        registry.mcp_registry = mock_registry

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value={"success": True, "result": "design-generated"})
        mock_executor_class.return_value = mock_executor

        tool = CallMCPTool(registry=registry)

        # 1. dimensions -> design_type
        await tool.execute(
            server_name="canva",
            tool_name="generate-design",
            arguments={"query": "Spikeball club poster", "dimensions": "poster"}
        )
        assert mock_executor.execute.call_args[0][1]["design_type"] == "poster"

        # 2. prompt -> query, and infer design_type from query text
        mock_executor.reset_mock()
        await tool.execute(
            server_name="canva",
            tool_name="generate-design",
            arguments={"prompt": "Spikeball tournament flyer for campus"}
        )
        assert mock_executor.execute.call_args[0][1]["query"] == "Spikeball tournament flyer for campus"
        assert mock_executor.execute.call_args[0][1]["design_type"] == "flyer"

        # 3. Fallback design_type to poster when no keyword matches
        mock_executor.reset_mock()
        await tool.execute(
            server_name="canva",
            tool_name="generate-design",
            arguments={"query": "something cool"}
        )
        assert mock_executor.execute.call_args[0][1]["design_type"] == "poster"

