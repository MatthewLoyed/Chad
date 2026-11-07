from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from src.core.orchestration.scout_mcp import gateway
from src.infrastructure.tools.execution.executor_strategy import MCPExecutor


def setup():
    tools = [{"name": "list_commits", "annotations": {"readOnlyHint": True},
              "inputSchema": {"type": "object", "properties": {"owner": {"type": "string"}}, "required": ["owner"]}},
             {"name": "create_issue", "annotations": {"readOnlyHint": False}},
             {"name": "get_unknown"},
             {"name": "contradictory", "annotations": {"readOnlyHint": True, "destructiveHint": True}}]
    server = SimpleNamespace(list_tools=AsyncMock(return_value=tools))
    mcp = SimpleNamespace(get_servers=lambda: {"github": server}, get=lambda n: server if n == "github" else None)
    return gateway(SimpleNamespace(mcp_registry=mcp)), server


@pytest.mark.asyncio
async def test_discovers_and_dispatches_only_validated_reads(monkeypatch):
    tool, server = setup()
    execute = AsyncMock(return_value={"success": True, "result": "commit"})
    monkeypatch.setattr(MCPExecutor, "execute", execute)
    found = await tool.ainvoke({"server_name": "github"})
    assert [t["name"] for t in found["tools"]] == ["list_commits"]
    assert found["blocked_count"] == 3
    bad = await tool.ainvoke({"server_name": "github", "tool_name": "list_commits"})
    assert bad["dispatched"] is False
    execute.assert_not_awaited()
    good = await tool.ainvoke({"server_name": "github", "tool_name": "list_commits", "arguments": {"owner": "example"}})
    assert good["success"]
    execute.assert_awaited_once()
    # Revocation after discovery must be enforced on the next call.
    server.list_tools.return_value[0]["annotations"]["readOnlyHint"] = False
    blocked = await tool.ainvoke({"server_name": "github", "tool_name": "list_commits", "arguments": {"owner": "example"}})
    assert blocked["dispatched"] is False
    assert execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["create_issue", "get_unknown", "contradictory", "invented"])
async def test_writes_and_unknowns_never_dispatch(name, monkeypatch):
    tool, _ = setup()
    execute = AsyncMock()
    monkeypatch.setattr(MCPExecutor, "execute", execute)
    blocked = await tool.ainvoke({"server_name": "github", "tool_name": name})
    assert blocked["failure_class"] == "blocked" and blocked["dispatched"] is False
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_scout_binding_and_execution_path(monkeypatch):
    from src.core.orchestration.nodes.scout_node import _bind_scout_tools, _execute_scout_tool
    tool, server = setup()
    mcp = SimpleNamespace(get_servers=lambda: {"github": server}, get=lambda name: server)
    registry = SimpleNamespace(mcp_registry=mcp, get_langchain_tool_by_name=lambda name: None, _tools={})
    _, mapping = _bind_scout_tools({"tools": [{"name": "call_mcp_tool"}]}, registry, [])
    assert "scout_mcp_read" in mapping and "call_mcp_tool" not in mapping
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(MCPExecutor, "execute", execute)
    result = await _execute_scout_tool("scout_mcp_read", {"server_name": "github", "tool_name": "list_commits",
        "arguments": {"owner": "example"}}, mapping, registry, {}, "test")
    assert result["success"]
    execute.assert_awaited_once()


from src.core.orchestration.mcp_classification import GITHUB_READS, GITHUB_WRITES, read_allowed


@pytest.mark.parametrize("name", sorted(GITHUB_READS | GITHUB_WRITES))
def test_every_legacy_github_operation_classified(name):
    server = SimpleNamespace(name="github", command=["npx", "-y", "@modelcontextprotocol/server-github"])
    assert read_allowed(server, {"name": name}) is (name in GITHUB_READS)
    assert len(GITHUB_READS | GITHUB_WRITES) == 26
    assert not GITHUB_READS & GITHUB_WRITES


def test_github_audit_cannot_authorize_unknown_or_other_server():
    server = SimpleNamespace(name="github", command=["npx", "-y", "@modelcontextprotocol/server-github"])
    assert not read_allowed(server, {"name": "get_new_tool", "annotations": {"readOnlyHint": True}})
    assert not read_allowed(server, {"name": "create_issue", "annotations": {"readOnlyHint": True}})
    assert not read_allowed(server, {"name": "list_commits", "annotations": {"readOnlyHint": False}})
    assert not read_allowed(SimpleNamespace(name="github", command=["other-server"]), {"name": "list_commits"})


@pytest.mark.asyncio
async def test_legacy_github_gateway_executes_read_and_blocks_write(monkeypatch):
    tools = [{"name": n, "inputSchema": {"type": "object"}} for n in GITHUB_READS | GITHUB_WRITES]
    server = SimpleNamespace(name="github", command=["npx", "-y", "@modelcontextprotocol/server-github"],
                             list_tools=AsyncMock(return_value=tools))
    registry = SimpleNamespace(mcp_registry=SimpleNamespace(get_servers=lambda: {"github": server}, get=lambda n: server))
    tool = gateway(registry)
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(MCPExecutor, "execute", execute)
    found = await tool.ainvoke({"server_name": "github"})
    assert len(found["tools"]) == 14 and found["blocked_count"] == 12
    assert (await tool.ainvoke({"server_name": "github", "tool_name": "list_commits"}))["success"]
    for name in GITHUB_WRITES:
        assert (await tool.ainvoke({"server_name": "github", "tool_name": name}))["dispatched"] is False
    execute.assert_awaited_once()
