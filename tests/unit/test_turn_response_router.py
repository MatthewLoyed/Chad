"""Regression tests for TurnResponseRouter <-> ToolCoordinator wiring (#641).

The voice-mode legacy tool-call flow (voice_session.py -> TurnResponseRouter)
called `tool_coord.execute_tool(...)` / `tool_coord.execute_tool_batch(...)` and
read `response['args']` — but ToolCoordinator only exposes `execute_single` /
`execute_batch`, taking a dict with an 'id'/'name'/'params' shape (params, not
args). Every voice-mode tool call silently failed, caught by the router's own
except block as a generic "Tool execution failed" result. These tests pin the
correct method names, call shape, and 'params' key so this can't regress silently
again.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.conversation.response_router import TurnResponseRouter
from src.core.orchestration.coordinators.tool_coordinator import ToolExecutionResult


def _turn():
    return SimpleNamespace(turn_id="t1", tool_count=0, tools_executed=[])


@pytest.mark.asyncio
async def test_handle_tool_call_uses_execute_single_with_params_key():
    tool_coord = SimpleNamespace(
        execute_single=AsyncMock(return_value=ToolExecutionResult(
            success=True, result={"ok": True}, tool_name="get_datetime", tool_id="call-1")),
    )
    state_coord = SimpleNamespace(transition_to=AsyncMock())
    router = TurnResponseRouter(tool_coord=tool_coord, state_coord=state_coord, event_bus=SimpleNamespace())

    turn = _turn()
    pending_calls, pending_responses = [], []
    response = {"type": "tool_call", "name": "get_datetime", "id": "call-1", "params": {"tz": "UTC"}}

    await router.handle_tool_call(response, turn, pending_calls, pending_responses)

    tool_coord.execute_single.assert_awaited_once_with(
        {"id": "call-1", "name": "get_datetime", "params": {"tz": "UTC"}})
    assert pending_responses == [{
        "tool_name": "get_datetime", "tool_id": "call-1",
        "result": {"ok": True}, "success": True,
    }]
    assert turn.tool_count == 1
    assert turn.tools_executed == ["get_datetime"]


@pytest.mark.asyncio
async def test_handle_tool_call_ignores_legacy_args_key():
    """A response using the old (wrong) 'args' key must not silently lose params —
    only 'params' is the real key response_harvester.py emits."""
    tool_coord = SimpleNamespace(execute_single=AsyncMock(return_value=ToolExecutionResult(
        success=True, result=None, tool_name="x", tool_id="c1")))
    state_coord = SimpleNamespace(transition_to=AsyncMock())
    router = TurnResponseRouter(tool_coord=tool_coord, state_coord=state_coord, event_bus=SimpleNamespace())

    response = {"type": "tool_call", "name": "x", "id": "c1", "args": {"should": "be ignored"},
                "params": {"real": "param"}}
    await router.handle_tool_call(response, _turn(), [], [])

    tool_coord.execute_single.assert_awaited_once_with(
        {"id": "c1", "name": "x", "params": {"real": "param"}})


@pytest.mark.asyncio
async def test_handle_tool_call_reports_failure_without_raising():
    tool_coord = SimpleNamespace(execute_single=AsyncMock(side_effect=RuntimeError("boom")))
    state_coord = SimpleNamespace(transition_to=AsyncMock())
    router = TurnResponseRouter(tool_coord=tool_coord, state_coord=state_coord, event_bus=SimpleNamespace())

    pending_responses = []
    await router.handle_tool_call(
        {"type": "tool_call", "name": "x", "id": "c1", "params": {}}, _turn(), [], pending_responses)

    assert pending_responses[0]["success"] is False
    assert "boom" in pending_responses[0]["error"]


@pytest.mark.asyncio
async def test_handle_tool_batch_uses_execute_batch():
    results = [
        ToolExecutionResult(success=True, result={"a": 1}, tool_name="tool_a", tool_id="1"),
        ToolExecutionResult(success=False, result=None, tool_name="tool_b", tool_id="2", error="nope"),
    ]
    tool_coord = SimpleNamespace(execute_batch=AsyncMock(return_value=results))
    state_coord = SimpleNamespace(transition_to=AsyncMock())
    router = TurnResponseRouter(tool_coord=tool_coord, state_coord=state_coord, event_bus=SimpleNamespace())

    calls = [
        {"id": "1", "name": "tool_a", "params": {}},
        {"id": "2", "name": "tool_b", "params": {}},
    ]
    turn = _turn()
    pending_responses = []
    await router.handle_tool_batch(
        {"type": "tool_call_batch", "calls": calls}, turn, [], pending_responses)

    tool_coord.execute_batch.assert_awaited_once_with(calls)
    assert turn.tool_count == 2
    assert turn.tools_executed == ["tool_a", "tool_b"]
    assert pending_responses[0]["success"] is True
    assert pending_responses[1]["error"] == "nope"
