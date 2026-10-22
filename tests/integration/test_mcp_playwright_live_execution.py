"""Live MCP Playwright tool execution for issue #216
("MCP tool execution: Playwright MCP tool invoked, result returned").

Unlike test_call_mcp_tool.py (which mocks MCPExecutor entirely to test the
policy/dispatch layer), this spawns the REAL configured Playwright MCP server
(npx @playwright/mcp, per config.yaml mcp_servers.playwright) and drives one
tool call through the full production path: ToolRegistry.mcp_registry ->
CallMCPTool.execute() -> executor_strategy.MCPExecutor -> server.call_tool().

Requires Node/npx locally (to spawn @playwright/mcp) -- skips cleanly if
unavailable, per #216's acceptance criterion that live-infra integration
tests be clearly marked and degrade gracefully rather than fail CI.
"""
import shutil
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("npx") is None,
    reason="requires Node/npx to spawn the @playwright/mcp server (live integration test)",
)


@pytest.fixture
async def playwright_registry():
    from src.core.tools.mcp_registry import MCPRegistry
    from src.shared.config_manager import ConfigManager

    servers_cfg = ConfigManager.get("mcp_servers") or {}
    pw_cfg = servers_cfg.get("playwright")
    if not pw_cfg:
        pytest.skip("mcp_servers.playwright not configured in config.yaml")

    registry = MCPRegistry()
    registry.register(
        "playwright",
        command=pw_cfg["command"],
        env=pw_cfg.get("env", {}),
        config={k: v for k, v in pw_cfg.items() if k not in ("command", "env")},
    )
    try:
        yield registry
    finally:
        await registry.cleanup()


@pytest.mark.asyncio
async def test_playwright_server_lists_real_tools(playwright_registry):
    """The MCP handshake succeeds and the server advertises real Playwright tools."""
    tool_defs = await playwright_registry.get_all_tool_definitions(use_cache=False)
    assert tool_defs, "Playwright MCP server returned no tools -- connection/handshake failed"
    names = {t["name"] for t in tool_defs}
    assert any("navigate" in n for n in names), f"expected a navigate-style tool, got: {names}"


@pytest.mark.asyncio
async def test_call_mcp_tool_executes_real_navigate_and_returns_result(playwright_registry):
    """Full production path: CallMCPTool -> MCPExecutor -> real Playwright server ->
    a real browser navigation, with an actual result forwarded back."""
    from src.tools.system.mcp.call_mcp_tool import CallMCPTool

    tool_defs = await playwright_registry.get_all_tool_definitions(use_cache=False)
    navigate_name = next((t["name"] for t in tool_defs if "navigate" in t["name"]), None)
    if not navigate_name:
        pytest.skip("Playwright MCP server did not expose a navigate tool")

    # CallMCPTool only needs registry.mcp_registry to expose .get(server_name) --
    # MCPRegistry itself satisfies that directly (see its own docstring example).
    registry = type("_R", (), {})()
    registry.mcp_registry = playwright_registry

    tool = CallMCPTool(registry=registry)
    result = await tool.execute(
        server_name="playwright",
        tool_name=navigate_name,
        arguments={"url": "https://example.com"},
    )

    assert result["success"] is True, f"navigate call failed: {result.get('error')}"
    assert result.get("result") is not None
