from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.orchestration.nodes.scout_node import _bind_scout_tools, _execute_scout_tool
from src.core.orchestration.scout_bindings import build_dispatch_scout_tool, build_mcp_read_gateway
from src.core.orchestration.scout_tool_policy import scout_tool_allowed
from src.core.orchestration.turn.tool_selector import TurnToolSelector
from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
from src.infrastructure.tools.mcp_read_index import approved_reads, read_index
from src.infrastructure.tools.registry import ToolRegistry
from src.infrastructure.tools.schema.schema_builder import parameters_for_llm_from_tool


@pytest.fixture
def registry_and_server(monkeypatch):
    monkeypatch.delenv("CHAD_PINNED_TOOLS", raising=False)
    selector = Mock()
    selector.is_available.return_value = True
    selector.last_select_meta = {}
    monkeypatch.setattr("src.infrastructure.tools.registry._build_llm_selector", lambda: selector)
    monkeypatch.setattr("src.infrastructure.tools.selection.turn_selector.configured_selector", lambda _: selector)
    registry = ToolRegistry()
    registry._selector = Mock()
    registry._selector.retrieve_shortlist.return_value = ["get_calendar_events"]
    read = {
        "name": "list_commits", "description": "List commits in a repository",
        "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]},
    }
    server = SimpleNamespace(
        name="github", command=["npx", "@modelcontextprotocol/server-github@2025.4.8"],
        config={}, list_tools=AsyncMock(return_value=[
            read, {"name": "create_issue", "annotations": {"readOnlyHint": True}},
            {"name": "list_issues"},
        ]),
    )
    servers = {server.name: server}
    registry.mcp_registry = SimpleNamespace(get_servers=lambda: servers, get=servers.get)
    return registry, server, selector


@pytest.mark.asyncio
async def test_selected_read_reaches_scout_and_dispatches_with_live_policy(registry_and_server, monkeypatch):
    registry, server, selector = registry_and_server
    monkeypatch.setattr(registry._turn_selector, "_hybrid_enabled", lambda: True)
    selector.select.return_value = ["github__list_commits"]
    execute = AsyncMock(return_value={"success": True, "result": "fixture commit"})
    monkeypatch.setattr(MCPExecutor, "execute", execute)

    selection = await TurnToolSelector(registry).select("List commits on GitHub", "planning")
    names = {schema["name"] for schema in selection.schemas}
    assert "github__list_commits" in names
    assert "github__list_issues" not in names
    catalog = selector.select.call_args.args[1]
    assert "github__list_commits" in catalog and "github__create_issue" not in catalog

    dispatch = build_dispatch_scout_tool(registry, selection.schemas)
    schema = parameters_for_llm_from_tool(dispatch)
    assert "github__list_commits" in schema["properties"]["tools"]["items"]["enum"]
    request = await dispatch.ainvoke({"brief": "Get commits", "tools": ["github__list_commits"]})
    assert request["_dispatch_scout"]
    state = {"tools": selection.schemas, "scout_requested_tools": request["requested_tools"]}
    _, mapping = _bind_scout_tools(state, registry, [])
    assert "github__list_commits" in mapping
    assert "call_mcp_tool" not in mapping

    result = await _execute_scout_tool("github__list_commits", {"repo": "demo"}, mapping, registry, {}, "test")
    assert result["success"]
    execute.assert_awaited_once()
    assert execute.await_args.args[0]["name"] == "list_commits"

    server.list_tools.return_value[0]["annotations"] = {"readOnlyHint": False}
    blocked = await _execute_scout_tool("github__list_commits", {"repo": "demo"}, mapping, registry, {}, "test")
    assert blocked["binding_unavailable"] and blocked["dispatched"] is False
    execute.assert_awaited_once()
    assert "github__list_commits" not in registry.tools


@pytest.mark.asyncio
async def test_gateway_blocks_excluded_mutating_and_unknown_operations(registry_and_server, monkeypatch):
    registry, server, _ = registry_and_server
    server.config["excluded_tools"] = ["list_issues"]
    gateway = build_mcp_read_gateway(registry)
    execute = AsyncMock()
    monkeypatch.setattr(MCPExecutor, "execute", execute)

    found = await gateway.ainvoke({"server_name": "github"})
    assert [tool["name"] for tool in found["tools"]] == ["list_commits"]
    for operation in ("create_issue", "list_issues", "invented"):
        result = await gateway.ainvoke({"server_name": "github", "tool_name": operation})
        assert result["failure_class"] == "blocked" and result["dispatched"] is False
    invalid = await gateway.ainvoke({"server_name": "github", "tool_name": "list_commits"})
    assert invalid["failure_class"] == "bad_input" and invalid["dispatched"] is False
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_dispatch_binds_only_confirmed_reads(registry_and_server):
    registry, _, _ = registry_and_server
    registry._tools["get_calendar_events"] = SimpleNamespace()
    registry._tools["kanban_board"] = SimpleNamespace(is_side_effecting=False)
    registry._tools["browser_snapshot"] = {"source": "mcp", "annotations": {"readOnlyHint": True}}
    registry.get_langchain_tool_by_name = lambda name: SimpleNamespace(name=name) if name in registry.tools else None
    menu = [{"name": name} for name in ("get_calendar_events", "kanban_board", "browser_snapshot")]
    dispatch = build_dispatch_scout_tool(registry, menu)
    for blocked_name in ("kanban_board", "browser_snapshot", "search_contacts"):
        denied = await dispatch.ainvoke({"brief": "Gather facts", "tools": [blocked_name]})
        assert denied["failure_class"] == "blocked"
        assert denied["dispatched"] is False
    result = await dispatch.ainvoke({"brief": "Read calendar", "tools": ["get_calendar_events"]})
    _, mapping = _bind_scout_tools({"scout_requested_tools": result["requested_tools"]}, registry, [])
    assert "get_calendar_events" in mapping and "kanban_board" not in mapping
    assert not scout_tool_allowed("browser_snapshot", registry)


@pytest.mark.asyncio
async def test_stale_catalog_refreshes_after_server_replacement(registry_and_server):
    registry, server, _ = registry_and_server
    index = read_index(registry)
    await index.refresh()
    await index.refresh()
    server.list_tools.assert_awaited_once()
    old_tool = registry.tools["github__list_commits"]

    replacement = SimpleNamespace(
        name="github", command=["other-server"], config={},
        list_tools=AsyncMock(return_value=[{"name": "list_commits"}]),
    )
    registry.mcp_registry.get_servers()["github"] = replacement
    assert await index.live_binding(old_tool) is None
    await index.refresh()
    replacement.list_tools.assert_awaited_once()
    assert "github__list_commits" not in registry.tools


def test_generic_mcp_policy_requires_an_unambiguous_read_hint():
    server = SimpleNamespace(name="fixture")
    tools = [
        {"name": "fetch", "annotations": {"readOnlyHint": True}},
        {"name": "unknown"},
        {"name": "contradiction", "annotations": {"readOnlyHint": True, "destructiveHint": True}},
    ]
    assert set(approved_reads(server, tools)) == {"fetch"}


def test_existing_inspection_tools_are_available_on_builtin_connectors():
    browser = SimpleNamespace(name="playwright", command=["npx", "@playwright/mcp"])
    desktop = SimpleNamespace(name="windows-semantic-ui", command=["Sbroenne.WindowsMcp.exe"])
    assert set(approved_reads(browser, [{"name": "browser_snapshot"}, {"name": "browser_click"}])) == {
        "browser_snapshot",
    }
    assert set(approved_reads(desktop, [
        {"name": "ui_find"}, {"name": "ui_read"}, {"name": "ui_click"},
    ])) == {"ui_find", "ui_read"}
