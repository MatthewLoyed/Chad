from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from src.core.orchestration.nodes.scout_node import _bind_scout_tools
from src.core.orchestration.planner_observation import prepare_bindings, valid_phase_batch
from src.shared.config_manager import ConfigManager


def fixture_registry():
    async def read(**kwargs): return {"success": True, "count": 2}
    tools = {n: StructuredTool.from_function(coroutine=read, name=n, description="test fixture")
        for n in ("get_calendar_events", "add_calendar_event", "run_host_command", "search_for_tool", "read_file", "list_directory", "web_search", "manage_user_memory")}
    return SimpleNamespace(get_langchain_tool_by_name=tools.get, _tools={}), tools


def test_calendar_reaches_scout_first_binding_without_discovery():
    registry, tools = fixture_registry()
    bound, mapping = _bind_scout_tools({"tools": [{"name": "get_calendar_events"}, {"name": "add_calendar_event"}, {"name": "run_host_command"}]}, registry, [])
    assert "get_calendar_events" in mapping
    assert "add_calendar_event" not in mapping and "run_host_command" not in mapping


def test_planner_describes_tools_but_never_binds_task_reads(monkeypatch):
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: True if key == "planner.read_tools_enabled" else default))
    registry, tools = fixture_registry()
    control = SimpleNamespace(name="dispatch_scout")
    planning, decision, coverage, bound, reads = prepare_bindings({"tools": [{"name": n} for n in tools]}, registry, [], [control], [], True, False)
    names = {t.name for t in bound}
    assert "get_calendar_events" not in names and "dispatch_scout" in names
    assert "add_calendar_event" not in names and "run_host_command" not in names
    assert not valid_phase_batch([{"name": "get_calendar_events"}], reads, {"dispatch_scout"}, True)
    assert not valid_phase_batch([{"name": "get_calendar_events"}, {"name": "dispatch_scout"}], reads, {"dispatch_scout"}, True)


@pytest.mark.asyncio
async def test_planner_rejects_task_call_then_dispatches_scout(monkeypatch):
    from src.core.orchestration.nodes import planner_node as planner
    registry, tools = fixture_registry()
    monkeypatch.setattr(ConfigManager,"get",staticmethod(lambda key, default=None: True if key == "planner.read_tools_enabled" else default))
    async def lifecycle(name,args,run):return await run()
    monkeypatch.setattr(planner,"run_with_tool_lifecycle",lifecycle)
    class Model:
        def __init__(self): self.responses=iter([AIMessage(content="",tool_calls=[{"name":"get_calendar_events","args":{},"id":"read"}]),AIMessage(content="",tool_calls=[{"name":"dispatch_scout","args":{"brief":"Inspect Calendar facts","tools":[]},"id":"dispatch"}])]);self.bindings=[]
        def copy(self,**kwargs):return self
        def bind_tools(self,tools):self.bindings.append({t.name for t in tools});return self
        async def ainvoke(self,*args,**kwargs):return next(self.responses)
    model=Model()
    result=await planner._run_planner_tool_loop(model, {"tools":[{"name":"get_calendar_events"}],"messages":[]}, {},
        planning_base="test",menu=[],allow_ask=False,allow_dispatch=True,tool_registry=registry)
    assert "get_calendar_events" not in model.bindings[0]
    assert result.get("scout_gather_request")
