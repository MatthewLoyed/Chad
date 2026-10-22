"""Offline boundary regressions from the refined Meet GUI trace; no live actions."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from mcp.types import TextContent

from src.core.orchestration.agents.agent_utils import create_fault_tolerant_tool_node
from src.core.orchestration.handoffs import create_handoff_tools, create_subagent_adapter
from src.core.orchestration.nodes.planner_node import _normalize_plan_steps
from src.core.orchestration.nodes.plan_parser import _next_actionable_step
from src.core.orchestration.nodes.tool_node import make_tool_node
from src.core.tools.mcp_registry import MCPServerEntry
from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
from src.tools.system.propose_plan import ProposePlanTool


@pytest.mark.asyncio
@pytest.mark.parametrize("wire_format", ["dict", "object"])
async def test_snapshot_file_hydration_preserves_refs_and_visible_values(tmp_path, monkeypatch, wire_format):
    import src.infrastructure.tools.execution.executor_strategy as execution

    (tmp_path / "page.yml").write_text(
        '- generic [ref=e1]:\n'
        '  - button "New meeting" [ref=e2]\n'
        '  - generic [ref=e3]: meet.example/room-123\n', encoding="utf-8",
    )
    monkeypatch.setattr(execution, "PROJECT_ROOT", str(tmp_path))
    executor = MCPExecutor()
    monkeypatch.setattr(executor, "_browser_safety_gate", lambda *args: None)
    monkeypatch.setattr(execution, "_policy_approval_gate", lambda *args: None)
    server = MagicMock(name="fake_server")
    server.name = "playwright"
    block = TextContent(type="text", text="### Snapshot\n- [Snapshot](./page.yml)")
    if wire_format == "dict":
        # Use the real producer boundary, not an assumed MCP return shape.
        entry = MCPServerEntry.__new__(MCPServerEntry)
        entry.session = MagicMock()
        entry.session.call_tool = AsyncMock(return_value=MagicMock(content=[block]))
        entry.config = {}
        server.call_tool = entry.call_tool
    else:
        server.call_tool = AsyncMock(return_value=[block])
    result = await executor.execute({"name": "browser_snapshot", "mcp_server": server}, {})
    text = "\n".join(p.get("text", "") for p in result["result"])
    assert result["success"] is True
    assert "### Interactive Elements (Set-of-Mark)" in text
    assert '[ref=e2]' in text
    assert "meet.example/room-123" in text
    assert "[Snapshot](./page.yml)" not in text


@pytest.mark.asyncio
async def test_unreadable_snapshot_is_explicitly_incomplete(tmp_path, monkeypatch):
    import src.infrastructure.tools.execution.executor_strategy as execution
    monkeypatch.setattr(execution, "PROJECT_ROOT", str(tmp_path))
    executor = MCPExecutor()
    monkeypatch.setattr(executor, "_browser_safety_gate", lambda *args: None)
    monkeypatch.setattr(execution, "_policy_approval_gate", lambda *args: None)
    server = MagicMock()
    server.name = "playwright"
    server.call_tool = AsyncMock(return_value=[{"type": "text", "text": "- [Snapshot](./missing.yml)"}])
    result = await executor.execute({"name": "browser_snapshot", "mcp_server": server}, {})
    assert "OBSERVATION INCOMPLETE" in str(result)
    assert "without a filename" in str(result)


@tool
async def screenshot_fixture() -> dict:
    """Return the same envelope shape as a successful browser screenshot."""
    return {"success": True, "_browser_tool": True, "result": [
        {"type": "text", "text": "Screenshot saved at screenshot.png"},
        {"type": "image", "data": "PIXELS" * 30000, "mimeType": "image/png"},
    ]}


@pytest.mark.asyncio
async def test_screenshot_through_graph_tool_node_never_serializes_pixels_into_context():
    from langgraph.graph import END, StateGraph
    from src.core.orchestration.graph.state import ChadState

    node = make_tool_node(create_fault_tolerant_tool_node([screenshot_fixture]))
    workflow = StateGraph(ChadState)
    workflow.add_node("tools", node)
    workflow.set_entry_point("tools")
    workflow.add_edge("tools", END)
    result = await workflow.compile().ainvoke({"messages": [AIMessage(content="", tool_calls=[
        {"name": "screenshot_fixture", "args": {}, "id": "shot"},
    ])]}, {})
    text = result["messages"][-1].content
    assert "PIXELS" not in text
    assert "truncated" not in text
    payload = json.loads(text.removeprefix("[TOOL_DATA]\n"))
    assert payload["success"] is True
    assert "screenshot.png" in text
    assert "Do not infer image contents" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_progress", "running", "completed", "nonsense", None])
async def test_new_plan_status_cannot_skip_first_step(status):
    original = [{"description": "first", "tools": [], "status": status},
                {"description": "second", "tools": []}]
    proposed = await ProposePlanTool().execute(steps=original)
    normalized = _normalize_plan_steps(proposed["steps"])
    assert _next_actionable_step(normalized)["id"] == "step_1"
    assert all(step["status"] == "pending" for step in normalized)
    assert original[0]["status"] == status
    assert "id" not in original[0]
    # Legacy/repaired planner payloads also pass through normalization directly.
    assert _normalize_plan_steps(original)[0]["status"] == "pending"


@pytest.fixture
def handoff_runtime(monkeypatch):
    import src.core.orchestration.handoffs as handoffs
    monkeypatch.setattr(handoffs, "_swap_for_subagent", AsyncMock(return_value=None))
    monkeypatch.setattr(handoffs, "_swap_back", AsyncMock())
    bus = MagicMock()
    monkeypatch.setattr(handoffs.EventBus, "get_instance", lambda: bus)
    return bus


def delegate_state(name):
    return {"messages": [AIMessage(content="", tool_calls=[
        {"name": f"delegate_to_{name}", "args": {"task": "perform task"}, "id": "delegate"},
    ])]}


def specialist_result(terminal):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=None)
    graph.ainvoke = AsyncMock(return_value={
        "messages": [AIMessage(content="Captured a room link; remaining work is incomplete.")],
        "budget_exceeded": True, "browser_budget_exhausted": terminal,
    })
    return graph


@pytest.mark.asyncio
async def test_terminal_browser_budget_crosses_handoff_and_blocks_reentry_only_for_browser(handoff_runtime):
    graph = specialist_result(True)
    node = create_subagent_adapter("browser_agent", graph)
    config = {"configurable": {"thread_id": "budget-boundary"}}
    result = await node(delegate_state("browser_agent"), config)
    assert result["browser_budget_exhausted"] is True
    assert "TOOL_FAILED[blocked]" in result["messages"][0].content
    assert "Captured a room link" in result["messages"][0].content
    assert handoff_runtime.publish.call_args.args[0].success is False
    await node({**delegate_state("browser_agent"), **result,
                "messages": delegate_state("browser_agent")["messages"]}, config)
    assert graph.ainvoke.await_count == 1
    desktop = specialist_result(False)
    desktop_node = create_subagent_adapter("ui_automation_agent", desktop)
    await desktop_node({**delegate_state("ui_automation_agent"), "browser_budget_exhausted": True}, config)
    desktop.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_step_cap_does_not_latch_global_browser_budget(handoff_runtime):
    graph = specialist_result(False)
    node = create_subagent_adapter("browser_agent", graph)
    result = await node(delegate_state("browser_agent"), {"configurable": {"thread_id": "local-cap"}})
    assert not result.get("browser_budget_exhausted")
    assert "PARTIAL PROGRESS" in result["messages"][0].content
    assert handoff_runtime.publish.call_args.args[0].success is False
    child_input = graph.ainvoke.call_args.args[0]
    assert child_input["budget_exceeded"] is False
    assert child_input["specialist_steps"] == 0


@pytest.mark.asyncio
async def test_callable_handoff_preserves_terminal_budget(handoff_runtime):
    graph = specialist_result(True)
    delegate = next(t for t in create_handoff_tools({"browser_agent": graph}) if t.name == "delegate_to_browser_agent")
    config = {"configurable": {"thread_id": "callable-cap"}}
    first = await delegate.coroutine("start task", config)
    second = await delegate.coroutine("retry task", config)
    assert "PARTIAL PROGRESS" in first
    assert "No additional browser actions" in second
    assert graph.ainvoke.await_count == 1


@pytest.mark.parametrize("name,args", [("browser_snapshot", {}), ("browser_scroll", {"amount": 200})])
def test_repeated_fresh_observations_do_not_get_false_reuse_advice(name, args):
    from src.core.tools.repetition import RepetitionGuard
    guard = RepetitionGuard()
    guard.note(name, args, success=True)
    guard.note("browser_click", {"target": "e2"}, success=True)
    hint = guard.note(name, args, success=True)
    assert not hint or "reuse it" not in hint
    assert guard.check(name, args) is None
