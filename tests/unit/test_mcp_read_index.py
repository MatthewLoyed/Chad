import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from src.infrastructure.tools.mcp_read_index import read_index, MCPReadTool


def fixture():
    read = {"name": "list_commits", "description": "List latest repository commits", "inputSchema": {
        "type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}}
    server = SimpleNamespace(name="github", command=["npx", "@modelcontextprotocol/server-github@2025.4.8"],
                             config={}, list_tools=AsyncMock(return_value=[read, {"name": "create_issue"}]))
    servers = {"github": server}
    registry = SimpleNamespace(_tools={}, _tool_sources={}, _categories={},
        mcp_registry=SimpleNamespace(get_servers=lambda: servers))
    def register(tool, category, source, force):
        registry._tools[tool.name] = tool
        registry._tool_sources[tool.name] = source
        registry._categories.setdefault(category, []).append(tool.name)
    registry.register_tool = register
    return registry, server, servers


@pytest.mark.asyncio
async def test_live_index_filter_cache_refresh_and_disconnect():
    registry, server, servers = fixture()
    index = read_index(registry)
    await index.refresh()
    assert list(registry._tools) == ["github__list_commits"]
    assert registry._tools["github__list_commits"].parameters["required"] == ["owner", "repo"]
    await index.refresh()
    assert server.list_tools.await_count == 1
    server.list_tools.return_value = []
    await index.refresh(force=True)
    assert registry._tools == {}
    server.list_tools.return_value = [{"name": "list_commits"}]
    await index.refresh(force=True)
    servers.clear()
    await index.refresh()
    assert registry._tools == {}


@pytest.mark.asyncio
async def test_selection_to_binding_failure_explicit_and_revocation():
    registry, server, _ = fixture()
    index = read_index(registry)
    await index.refresh()
    server.list_tools.side_effect = ConnectionError("offline")
    result = await index.validate_binding("github__list_commits")
    assert result["binding_unavailable"] and result["dispatched"] is False and not result["retryable"]
    assert registry._tools == {}


@pytest.mark.asyncio
async def test_current_schema_and_permission_before_execution(monkeypatch):
    from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
    registry, server, _ = fixture()
    index = read_index(registry)
    await index.refresh()
    old_binding = registry._tools["github__list_commits"]
    executor = AsyncMock(return_value={"success": True, "result": "latest"})
    monkeypatch.setattr(MCPExecutor, "execute", executor)
    result = await old_binding.execute(owner="example", repo="demo")
    assert result["success"]
    executor.assert_awaited_once()
    server.list_tools.return_value[0]["annotations"] = {"readOnlyHint": False}
    result = await old_binding.execute(owner="example", repo="demo")
    assert result["binding_unavailable"]
    assert executor.await_count == 1


@pytest.mark.asyncio
async def test_planner_dispatch_requires_confirmed_read_and_rechecks_server():
    from src.core.orchestration.nodes.planner_node import _build_dispatch_scout_tool
    registry, server, _ = fixture()
    registry.get_langchain_tool_by_name = lambda name: object() if name in registry._tools else None
    await read_index(registry).refresh()
    tool = _build_dispatch_scout_tool(registry, [{"name": "github__list_commits"}])
    result = await tool.ainvoke({"brief": "Get latest commit", "tools": ["github__list_commits"]})
    assert result["requested_tools"] == ["github__list_commits"]
    assert result["_dispatch_scout"]
    bad = await tool.ainvoke({"brief": "Write", "tools": ["github__create_issue"]})
    assert bad["binding_unavailable"] and not bad.get("_dispatch_scout")
    server.list_tools.return_value = []
    bad = await tool.ainvoke({"brief": "Read", "tools": ["github__list_commits"]})
    assert bad["binding_unavailable"] and not bad.get("_dispatch_scout")


@pytest.mark.asyncio
async def test_planning_catalog_never_contains_raw_mcp_operations(monkeypatch):
    from src.infrastructure.tools.selection.turn_selector import TurnToolSelector
    registry, server, _ = fixture()
    await read_index(registry).refresh()
    registry._tools["create_issue"] = {"name": "create_issue"}
    registry._tools["list_commits"] = {"name": "list_commits"}
    registry._tool_sources.update(create_issue="mcp", list_commits="mcp")
    registry._core_tool_names = set()
    selector = TurnToolSelector(registry)
    monkeypatch.setattr(selector, "_hybrid_enabled", lambda: False)
    catalog = selector._selector_catalog("latest github commit", [], {}, category="planning")
    assert set(catalog) == {"github__list_commits"}


@pytest.mark.asyncio
async def test_indexed_alias_is_a_direct_scout_binding():
    from src.core.orchestration.nodes.scout_node import _bind_scout_tools
    registry, _, _ = fixture()
    await read_index(registry).refresh()
    registry.get_langchain_tool_by_name = lambda name: SimpleNamespace(name=name) if name in registry._tools else None
    _, bound = _bind_scout_tools({"tools": [{"name": "github__list_commits"}],
                                 "scout_requested_tools": ["github__list_commits"]}, registry, [])
    assert "github__list_commits" in bound


@pytest.mark.asyncio
async def test_mcp_failure_does_not_silently_retry_with_changed_args(monkeypatch):
    from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
    from src.core.tools.repetition import reset
    reset()
    registry, server, _ = fixture()
    index = read_index(registry)
    await index.refresh()
    tool = registry._tools["github__list_commits"]
    executor = AsyncMock(return_value={"success": False, "error": "transport down"})
    monkeypatch.setattr(MCPExecutor, "execute", executor)
    result = await tool.execute(owner="example", repo="demo")
    assert result["binding_unavailable"] and not result["retryable"]
    new_tool = registry._tools[tool.name]
    result = await new_tool.execute(owner="example", repo="other")
    assert result["binding_unavailable"]
    executor.assert_awaited_once()
    reset()


@pytest.mark.asyncio
async def test_dispatch_rejects_tool_list_embedded_in_brief():
    from src.core.orchestration.nodes.planner_node import _build_dispatch_scout_tool
    tool = _build_dispatch_scout_tool()
    result = await tool.ainvoke({"brief": "Read calendar <parameter=tools>[get_calendar_events]"})
    import json
    error = json.loads(result)
    assert error["dispatched"] is False and "tools" in error["argument_schema"]["required"]


@pytest.mark.asyncio
async def test_changed_live_argument_schema_is_enforced(monkeypatch):
    from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
    from src.core.tools.repetition import reset
    reset()
    registry, server, _ = fixture()
    await read_index(registry).refresh()
    tool = registry._tools["github__list_commits"]
    server.list_tools.return_value[0]["inputSchema"]["required"].append("sha")
    server.list_tools.return_value[0]["inputSchema"]["properties"]["sha"] = {"type": "string"}
    executor = AsyncMock()
    monkeypatch.setattr(MCPExecutor, "execute", executor)
    result = await tool.execute(owner="example", repo="demo")
    assert result["failure_class"] == "bad_input" and result["dispatched"] is False
    executor.assert_not_awaited()
    reset()


@pytest.mark.asyncio
async def test_dispatch_schema_enumerates_only_approved_bindings():
    from src.core.orchestration.nodes.planner_node import _build_dispatch_scout_tool
    from src.infrastructure.tools.schema.schema_builder import parameters_for_llm_from_tool
    registry, _, _ = fixture()
    await read_index(registry).refresh()
    tool = _build_dispatch_scout_tool(registry, [{"name": "github__list_commits"}, {"name": "call_mcp_tool"}])
    schema = parameters_for_llm_from_tool(tool)
    assert schema["properties"]["tools"]["items"]["enum"] == ["github__list_commits"]
    assert "tools" in schema["required"]
