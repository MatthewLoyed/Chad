import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.routines.routine_builder import RoutineBuilder
from src.core.routines.workflow_executor import execute_workflow_steps
from src.shared.config_manager import ConfigManager


def test_builder_follows_supervisor_despite_stale_profile(monkeypatch):
    monkeypatch.setattr(ConfigManager, "get_profile", lambda: {"routine_builder_model": "small-model"})
    monkeypatch.setattr(ConfigManager, "get_active_model", lambda: "supervisor-model")
    assert ConfigManager.get_routine_builder_model() == "supervisor-model"


@pytest.mark.asyncio
async def test_failed_tool_stops_dependent_steps_and_reports_failed_event():
    registry = SimpleNamespace(
        _tools={"lookup": {}, "open": {}},
        execute_tool=AsyncMock(return_value={"success": False, "error": "Assignment missing"}),
    )
    bus = SimpleNamespace(publish=lambda event: events.append(event))
    events = []
    lines, ok = await execute_workflow_steps(registry, [
        {"tool_name": "lookup"}, {"tool_name": "open"},
    ], event_bus=bus)
    assert not ok
    assert registry.execute_tool.await_count == 1
    assert "Assignment missing" in lines[0]
    assert events[-1].success is False


@pytest.mark.asyncio
async def test_continue_after_missing_tool_still_reports_failure():
    registry = SimpleNamespace(_tools={}, execute_tool=AsyncMock())
    _, ok = await execute_workflow_steps(registry, [{"tool_name": "missing"}], abort_on_failure=False)
    assert not ok


def test_builder_rejects_partial_plan_with_unknown_tool():
    builder = RoutineBuilder.__new__(RoutineBuilder)
    raw = json.dumps([{"tool_name": "lookup"}, {"tool_name": "invented"}])
    assert builder._parse_steps(raw, {"lookup"}) is None


@pytest.mark.asyncio
async def test_builder_uses_injected_supervisor_adapter_and_full_schema():
    adapter = SimpleNamespace(generate=AsyncMock(return_value=SimpleNamespace(
        text='[{"tool_name":"lookup","params":{"assignment_id":5}}]', finish_reason="stop",
    )))
    schema = {"type": "object", "properties": {"assignment_id": {"type": "integer"}}, "required": ["assignment_id"]}
    result = await RoutineBuilder(adapter=adapter).draft("Look up assignment 5", [
        {"name": "lookup", "parameters": schema},
    ])
    assert result["success"]
    request = adapter.generate.call_args.kwargs
    assert json.dumps(schema, ensure_ascii=False) in request["messages"][1]["content"]
    assert request.get("tools") is None  # Drafting must never execute anything.


@pytest.mark.asyncio
async def test_builder_rejects_missing_required_parameter():
    adapter = SimpleNamespace(generate=AsyncMock(return_value=SimpleNamespace(
        text='[{"tool_name":"lookup","params":{}}]', finish_reason="stop",
    )))
    result = await RoutineBuilder(adapter=adapter).draft("Look up assignment", [
        {"name": "lookup", "parameters": {"type": "object", "required": ["assignment_id"]}},
    ])
    assert not result["success"]
    assert result["steps"] == []
    assert "assignment_id" in result["error"]


@pytest.mark.asyncio
async def test_command_selects_agent_supervisor_without_changing_active_session():
    from src.application.commands.routine_commands import DraftRoutineCommand

    adapter = SimpleNamespace(generate=AsyncMock(return_value=SimpleNamespace(
        text='[{"tool_name":"lookup","params":{}}]', finish_reason="stop",
    )))
    router = SimpleNamespace(get_model_for_category=Mock(return_value=SimpleNamespace(adapter=adapter)))
    registry = SimpleNamespace(list_catalog_entries=lambda: [{"name": "lookup", "parameters": {}}])
    facade = SimpleNamespace(_assistant=SimpleNamespace(
        tool_registry=registry, session_manager=SimpleNamespace(router=router),
    ))
    result = await DraftRoutineCommand(description="Look up the current time").execute(facade)
    assert result["success"]
    router.get_model_for_category.assert_called_once_with("agent")
    adapter.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_no_supervisor_does_not_fall_back_to_selector():
    from src.application.commands.routine_commands import DraftRoutineCommand

    registry = SimpleNamespace(list_catalog_entries=lambda: [{"name": "lookup"}])
    facade = SimpleNamespace(_assistant=SimpleNamespace(tool_registry=registry))
    result = await DraftRoutineCommand(description="Look up the time").execute(facade)
    assert not result["success"]
    assert "Supervisor" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [{"isError": True}, {"success": False, "result": "attempted"}])
async def test_returned_errors_are_failures_even_with_result_text(output):
    registry = SimpleNamespace(_tools={"lookup": {}}, execute_tool=AsyncMock(return_value=output))
    lines, ok = await execute_workflow_steps(registry, [{"tool_name": "lookup"}])
    assert not ok
    assert "failed" in lines[0]


@pytest.mark.asyncio
async def test_successful_steps_preserve_order_and_report_success():
    registry = SimpleNamespace(_tools={"lookup": {}, "open": {}}, execute_tool=AsyncMock(
        side_effect=[{"success": True, "result": "Found"}, {"success": True, "result": "Opened"}],
    ))
    lines, ok = await execute_workflow_steps(registry, [{"tool_name": "lookup"}, {"tool_name": "open"}])
    assert ok
    assert [call.args[0] for call in registry.execute_tool.await_args_list] == ["lookup", "open"]
    assert "Found" in lines[0] and "Opened" in lines[1]
