from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from src.infrastructure.tools.schema.argument_validation import validate_arguments
from src.infrastructure.tools.dispatch import dispatch_tool

SCHEMA = {"type": "object", "properties": {"limit": {"type": "integer"},
    "time_min": {"type": "string", "format": "date-time"}, "time_max": {"type": "string", "format": "date-time"}}}


@pytest.mark.asyncio
async def test_bad_dates_never_reach_executor_corrected_dates_do():
    executor = SimpleNamespace(execute=AsyncMock(return_value={"success": True, "count": 2}))
    registry = SimpleNamespace(_tools={"get_calendar_events": object()}, _tool_sources={},
        _extract_schema=lambda name, tool: {"parameters": SCHEMA},
        _routing_engine=SimpleNamespace(route_to_executor=lambda tool: executor))
    bad = {"time_min": '2026-11-06T00:00:00"', "time_max": '2026-11-07T00:00:00Z"'}
    for _ in range(2):
        result = await dispatch_tool(registry, "get_calendar_events", bad)
        assert result["success"] is False and result["dispatched"] is False
        assert result["failure_class"] == "bad_input"
        assert result["argument_schema"] == SCHEMA
    executor.execute.assert_not_awaited()
    good = {"time_min": "2026-11-07T00:00:00-06:00", "time_max": "2026-11-08T00:00:00-06:00"}
    result = await dispatch_tool(registry, "get_calendar_events", good)
    assert result["count"] == 2
    executor.execute.assert_awaited_once()


@pytest.mark.parametrize("args", [{"limit": "ten"}, {"time_min": "2026-02-30T00:00:00Z"},
    {"time_min": "2026-11-08T00:00:00Z", "time_max": "2026-11-07T00:00:00Z"}])
def test_schema_and_semantics(args):
    assert validate_arguments("get_calendar_events", args, SCHEMA)[1] is not None


def test_null_optional_normalization_does_not_invent_dates():
    args, error = validate_arguments("get_calendar_events", {"time_min": None}, SCHEMA)
    assert error is None and args == {}


@pytest.mark.asyncio
async def test_wrapper_type_error_returns_schema_feedback_not_exception():
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel
    from src.infrastructure.tools.schema.argument_validation import wrapper_feedback
    import json
    class Args(BaseModel):
        limit: int
    async def run(limit):
        raise AssertionError("Must not execute")
    tool = StructuredTool.from_function(coroutine=run, name="get_calendar_events", description="test",
        args_schema=Args, handle_validation_error=wrapper_feedback("get_calendar_events", SCHEMA))
    result = json.loads(await tool.ainvoke({"limit": "not an integer"}))
    assert result["dispatched"] is False
    assert result["argument_schema"] == SCHEMA
    assert result["validation_errors"][0]["path"] == "limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", ["-06:00", "+05:30", "Z"])
async def test_calendar_connector_preserves_valid_timezone_boundaries(offset):
    from unittest.mock import Mock
    from src.tools.workspace.calendar import GetCalendarEventsTool
    service = Mock()
    service.events.return_value.list.return_value.execute.return_value = {"items": []}
    tool = object.__new__(GetCalendarEventsTool)
    tool._manager = SimpleNamespace(get_calendar_service=lambda: service)
    start = "2026-11-07T00:00:00" + offset
    end = "2026-11-08T00:00:00" + offset
    result = await tool.execute(time_min=start, time_max=end)
    assert result["success"] is True
    params = service.events.return_value.list.call_args.kwargs
    assert params["timeMin"] == start
    assert params["timeMax"] == end
