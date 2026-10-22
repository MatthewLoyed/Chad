"""Unit tests for observation satiation guard in BrowserScrollTool."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tools.web.browser_scroll import BrowserScrollTool


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    server_entry = MagicMock()
    registry.mcp_registry = {"playwright": server_entry}
    return registry


@pytest.mark.asyncio
async def test_browser_scroll_boundary_warning(mock_registry):
    tool = BrowserScrollTool(registry=mock_registry)
    
    # Mock executor.execute for browser_evaluate to return atBottom=True
    with patch("src.infrastructure.tools.execution.executor_strategy.MCPExecutor") as MockExecutorClass:
        mock_executor = MockExecutorClass.return_value
        
        # 1. browser_evaluate returns atBottom: True
        mock_executor.execute = AsyncMock(side_effect=[
            {
                "success": True,
                "result": [{"type": "text", "text": '{"prevY": 1000, "currY": 2000, "maxY": 2000, "moved": true, "atBottom": true, "atTop": false}'}]
            },
            # 2. browser_wait_for
            {"success": True, "result": []},
            # 3. browser_snapshot
            {"success": True, "result": [{"type": "text", "text": "Page content here"}]}
        ])
        
        res = await tool.execute(direction="down")
        assert res["success"] is True
        text = res["result"][0]["text"]
        assert "[PAGE BOUNDARY REACHED]" in text
        assert "scrollY=2000/2000" in text


@pytest.mark.asyncio
async def test_browser_scroll_satiation_fast_fail(mock_registry):
    tool = BrowserScrollTool(registry=mock_registry)
    
    with patch("src.infrastructure.tools.execution.executor_strategy.MCPExecutor") as MockExecutorClass:
        mock_executor = MockExecutorClass.return_value
        mock_executor.execute = AsyncMock(side_effect=[
            # First scroll: hits boundary
            {
                "success": True,
                "result": [{"type": "text", "text": '{"prevY": 2000, "currY": 2000, "maxY": 2000, "moved": false, "atBottom": true, "atTop": false}'}]
            },
            {"success": True, "result": []},
            {"success": True, "result": [{"type": "text", "text": "Page content"}]},
            # Second scroll: stationary again
            {
                "success": True,
                "result": [{"type": "text", "text": '{"prevY": 2000, "currY": 2000, "maxY": 2000, "moved": false, "atBottom": true, "atTop": false}'}]
            },
        ])
        
        # 1st scroll hits boundary
        res1 = await tool.execute(direction="down")
        assert res1["success"] is True
        
        # 2nd scroll hits boundary again -> fast fails with SCROLL SATIATED
        res2 = await tool.execute(direction="down")
        assert res2["success"] is True
        assert "[PAGE BOUNDARY REACHED / SCROLL SATIATED]" in res2["result"][0]["text"]
        
        # 3rd scroll doesn't even call MCPExecutor -> instantly fast-fails
        res3 = await tool.execute(direction="down")
        assert res3["success"] is True
        assert "[PAGE BOUNDARY REACHED / SCROLL SATIATED]" in res3["result"][0]["text"]


@pytest.mark.asyncio
async def test_browser_scroll_about_blank_fast_fail(mock_registry):
    tool = BrowserScrollTool(registry=mock_registry)
    with patch("src.infrastructure.tools.execution.executor_strategy.MCPExecutor") as MockExecutorClass:
        mock_executor = MockExecutorClass.return_value
        mock_executor.execute = AsyncMock(return_value={
            "success": True,
            "result": [{"type": "text", "text": '{"url": "about:blank", "isBlank": true, "prevY": 0, "currY": 0, "maxY": 0, "moved": false, "atBottom": true, "atTop": true}'}]
        })
        res = await tool.execute(direction="down")
        assert res["success"] is False
        assert "about:blank" in res["error"]
        assert "browser_navigate" in res["error"]
