"""Calendar argument validation at the registry and LangGraph entry points."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from src.core.tools import repetition
from src.infrastructure.tools.registry import ToolRegistry
from src.infrastructure.tools.schema.schema_builder import ToolSchemaBuilder
from src.tools.workspace import calendar
from src.tools.workspace.calendar import GetCalendarEventsTool


@pytest.fixture
def calendar_registry():
    repetition.reset()
    tool = object.__new__(GetCalendarEventsTool)
    service = Mock()
    service.events.return_value.list.return_value.execute.return_value = {"items": []}
    tool._manager = SimpleNamespace(get_calendar_service=Mock(return_value=service))
    registry = object.__new__(ToolRegistry)
    registry._tools = {tool.name: tool}
    registry._event_bus = None
    registry._schema_builder = ToolSchemaBuilder(registry)

    async def execute(tool, parameters):
        return await tool.execute(**parameters)

    executor = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    registry._routing_engine = SimpleNamespace(route_to_executor=Mock(return_value=executor))
    return registry, tool, executor, service


@pytest.mark.asyncio
async def test_invalid_retries_skip_dispatch_and_corrected_call_dispatches_once(calendar_registry):
    registry, tool, executor, service = calendar_registry
    invalid = {"time_min": '2026-06-01T12:00:00"', "time_max": "2026-06-01T13:00:00Z"}

    for _ in range(2):
        result = await registry.execute_tool(tool.name, invalid)
        assert result["failure_class"] == "bad_input"
        assert result["dispatched"] is False
        assert result["validation_errors"][0]["path"] == "time_min"
        assert result["argument_schema"] == tool.get_schema()["parameters"]
        assert "2026-06-01" not in json.dumps(result)

    registry._routing_engine.route_to_executor.assert_not_called()
    executor.execute.assert_not_awaited()
    tool._manager.get_calendar_service.assert_not_called()

    corrected = {
        "time_min": "2026-06-01T06:00:00-06:00",
        "time_max": "2026-06-01T07:00:00-06:00",
        "limit": None,
    }
    result = await registry.execute_tool(tool.name, corrected)

    assert result["success"] is True
    executor.execute.assert_awaited_once_with(
        tool, {"time_min": corrected["time_min"], "time_max": corrected["time_max"],
               "tool_registry": registry},
    )
    calendar_list = service.events.return_value.list
    calendar_list.assert_called_once()
    assert calendar_list.call_args.kwargs["timeMin"] == corrected["time_min"]
    assert calendar_list.call_args.kwargs["timeMax"] == corrected["time_max"]
    assert calendar_list.call_args.kwargs["maxResults"] == 10
    assert corrected["limit"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"time_min": "2026-06-01T12:00:00"},
        {"time_min": "2026-02-30T12:00:00Z"},
        {"time_min": "2026-06-01T12:00:00+05:90"},
        {"time_min": "2026-06-01T12:00:00Z", "time_max": "2026-06-01T06:00:00-06:00"},
        {"time_min": "2026-06-01T12:00:00Z", "time_max": "2026-06-01T11:59:00Z"},
        {"limit": "5"},
        {"unknown": None},
        {"context": "model supplied", "tool_registry": "not the live registry"},
    ],
)
async def test_invalid_arguments_are_rejected_by_registry(calendar_registry, arguments):
    registry, tool, executor, _ = calendar_registry

    result = await registry.execute_tool(tool.name, arguments)

    assert result["failure_class"] == "bad_input"
    assert result["dispatched"] is False
    executor.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"limit": "5"}, "limit"),
        ({"time_min": 17}, "time_min"),
        ({"time_mim": "2026-06-01T12:00:00Z"}, "time_mim"),
        ({"time_min": "2026-06-01T12:00:00"}, "time_min"),
    ],
)
async def test_tool_node_returns_argument_feedback(calendar_registry, arguments, field):
    registry, tool, executor, _ = calendar_registry
    wrapped = registry.get_langchain_tool_by_name(tool.name)
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([wrapped]))
    graph.set_entry_point("tools")
    result = await graph.compile().ainvoke({"messages": [AIMessage(content="", tool_calls=[
        {"name": tool.name, "args": arguments, "id": "calendar-arguments"},
    ])]})

    message = result["messages"][-1]
    payload = json.loads(message.content)
    assert message.tool_call_id == "calendar-arguments"
    assert payload["dispatched"] is False
    assert payload["failure_class"] == "bad_input"
    assert payload["validation_errors"][0]["path"] == field
    executor.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", ["Z", "+05:30", "-06:00"])
async def test_wrapped_call_preserves_valid_offsets(calendar_registry, offset):
    registry, tool, executor, service = calendar_registry
    wrapped = registry.get_langchain_tool_by_name(tool.name)
    start = "2026-06-01T12:00:00" + offset
    end = "2026-06-01T13:00:00" + offset

    result = await wrapped.ainvoke({"time_min": start, "time_max": end, "hours_ahead": None})

    assert result["success"] is True
    executor.execute.assert_awaited_once()
    params = service.events.return_value.list.call_args.kwargs
    assert params["timeMin"] == start
    assert params["timeMax"] == end


@pytest.mark.asyncio
async def test_registry_preserves_trusted_executor_metadata(calendar_registry):
    registry, tool, executor, _ = calendar_registry
    context = object()
    memory = object()
    arguments = {"tool_registry": registry, "context": context, "memory_engine": memory}

    result = await registry.execute_tool(tool.name, arguments)

    assert result["success"] is True
    executor.execute.assert_awaited_once_with(tool, arguments)


@pytest.mark.asyncio
async def test_direct_read_checks_bounds_before_opening_calendar_service(calendar_registry):
    _, tool, _, _ = calendar_registry

    result = await tool.execute(time_min="2026-06-01")

    assert result["failure_class"] == "bad_input"
    assert result["dispatched"] is False
    tool._manager.get_calendar_service.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"day": "2026-02-30"},
    {"day": "2026-11-01T00:00:00"},
    {"day": "tomorrow"},
    {"day": "9999-12-31"},
    {"day": "today", "time_min": "2026-11-01T00:00:00Z"},
    {"day": "today", "hours_ahead": 24},
])
async def test_invalid_day_stops_before_calendar_dispatch(calendar_registry, arguments):
    registry, tool, executor, _ = calendar_registry

    result = await registry.execute_tool(tool.name, arguments)

    assert result["failure_class"] == "bad_input"
    assert result["dispatched"] is False
    assert result["validation_errors"][0]["path"] == "day"
    executor.execute.assert_not_awaited()
    tool._manager.get_calendar_service.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("day", "start_offset", "end_offset"), [
    ("2026-03-08", "-06:00", "-05:00"),
    ("2026-11-01", "-05:00", "-06:00"),
    ("2026-12-31", "-06:00", "-06:00"),
])
async def test_wrapped_day_resolves_each_local_midnight(calendar_registry, monkeypatch, day, start_offset, end_offset):
    registry, tool, executor, service = calendar_registry
    first = datetime.fromisoformat(day).date()
    offsets = iter([start_offset, end_offset])

    class LocalDateTime(datetime):
        def astimezone(self, tz=None):
            if self.tzinfo is not None:
                return super().astimezone(tz)
            assert tz is None
            return datetime.fromisoformat(self.isoformat() + next(offsets))

    monkeypatch.setattr(calendar, "datetime", LocalDateTime)
    monkeypatch.setattr(calendar, "_get_local_timezone", lambda: timezone.utc)
    result = await registry.get_langchain_tool_by_name(tool.name).ainvoke({"day": day, "search_query": "Planning"})

    assert result["success"]
    executor.execute.assert_awaited_once()
    params = service.events.return_value.list.call_args.kwargs
    assert params["timeMin"] == first.isoformat() + "T00:00:00" + start_offset
    assert params["timeMax"] == (first + timedelta(days=1)).isoformat() + "T00:00:00" + end_offset
    assert params["q"] == "Planning"


@pytest.mark.asyncio
async def test_today_uses_the_host_local_date(calendar_registry, monkeypatch):
    registry, tool, _, service = calendar_registry
    fixed_now = datetime(2026, 11, 10, 1, 30, tzinfo=timezone.utc)
    local_day = fixed_now.astimezone().date()
    next_day = local_day + timedelta(days=1)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(calendar, "datetime", FrozenDateTime)
    result = await registry.execute_tool(tool.name, {"day": "today"})

    assert result["success"]
    params = service.events.return_value.list.call_args.kwargs
    assert params["timeMin"].startswith(local_day.isoformat() + "T00:00:00")
    assert params["timeMax"].startswith(next_day.isoformat() + "T00:00:00")
