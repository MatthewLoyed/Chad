from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from src.core.orchestration.nodes import scout_node as scout


class Model:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.bindings = []
        self.calls = []
    def copy(self, **kwargs): return self
    def bind_tools(self, tools):
        self.bindings.append({t.name for t in tools})
        return self
    async def ainvoke(self, messages, config=None):
        self.calls.append(list(messages))
        return next(self.responses)


def call(name, args, ident="t"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident}])


def setup_node(monkeypatch, model):
    from src.application.events.event_bus import EventBus
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    monkeypatch.setattr(EventBus, "get_instance", lambda: MagicMock())
    monkeypatch.setattr(SystemPromptBuilder, "build", lambda **kwargs: "Scout role")
    monkeypatch.setattr(scout, "_maybe_unload_main_model", AsyncMock())
    registry = MagicMock()
    registry.get_all_schemas.return_value = []
    registry.get_langchain_tool_by_name.side_effect = lambda n: SimpleNamespace(name=n, description="read/search")
    monkeypatch.setattr(scout, "_execute_scout_tool", AsyncMock(return_value={"success": True, "result": "Observed fact"}))
    return scout.make_scout_node(model, registry, [])


@pytest.mark.asyncio
async def test_empty_first_response_keeps_research_tools_enabled(monkeypatch):
    item = {"question": "Public website", "status": "verified", "finding": "Observed fact", "source": "web_search", "source_call_id": "lookup"}
    model = Model([AIMessage(content=""), call("web_search", {"queries": ["official website"]}, "lookup"), call("finish_scout_report", {"items": [item]})])
    node = setup_node(monkeypatch, model)
    out = await node({"messages": [HumanMessage(content="Find website")], "scout_gather_request": "Verify public website"}, {})
    assert len(model.calls[0]) == 2
    assert isinstance(model.calls[0][0], SystemMessage)
    assert isinstance(model.calls[0][1], HumanMessage)
    assert "[HARNESS: gather phase]" in model.calls[1][-1].content
    assert len(model.bindings) == 1
    assert "web_search" in model.bindings[0]
    assert "No more tool calls" not in str(model.calls)
    assert out["scout_evidence"][0]["items"] == [item]


@pytest.mark.asyncio
async def test_report_phase_keeps_finish_tool_and_forbids_other_execution():
    item = {"question": "App installed", "status": "unavailable", "finding": "No approved inspection source", "source": ""}
    model = Model([call("finish_scout_report", {"items": [item]})])
    messages = [SystemMessage(content="Scout role"), HumanMessage(content="Verify app")]
    items = await scout._force_report_turn(model, messages, {})
    assert model.bindings == [{"finish_scout_report"}]
    assert "[HARNESS: report phase]" in model.calls[0][-1].content
    assert "No more tool calls" not in model.calls[0][-1].content
    assert items == [item]


@pytest.mark.asyncio
async def test_repeated_empty_responses_are_bounded(monkeypatch):
    model = Model([AIMessage(content=""), AIMessage(content=""), AIMessage(content="")])
    node = setup_node(monkeypatch, model)
    out = await node({"messages": [HumanMessage(content="Verify app")], "scout_gather_request": "Verify app"}, {})
    assert len(model.calls) == 3  # initial, one gather retry, one report-only attempt
    assert model.bindings[-1] == {"finish_scout_report"}
    assert out["scout_evidence"][0]["status"] == "unstructured"


@pytest.mark.asyncio
async def test_report_phase_rejects_research_call_without_running_it():
    model = Model([call("web_search", {"queries": ["must not run"]})])
    messages = [SystemMessage(content="Scout")]
    assert await scout._force_report_turn(model, messages, {}) == []
    assert "Only finish_scout_report" in messages[-1].content


@pytest.mark.asyncio
async def test_budget_exhaustion_switches_to_one_report_attempt(monkeypatch):
    from src.shared.config_manager import ConfigManager
    original = ConfigManager.get
    monkeypatch.setattr(ConfigManager, "get", lambda key, default=None: 1 if key in {"planner.scout_max_tool_calls", "planner.scout_tool_calls_wrapup"} else original(key, default))
    model = Model([call("web_search", {"queries": ["first"]}, "one"), call("web_search", {"queries": ["second"]}, "two"), AIMessage(content="")])
    node = setup_node(monkeypatch, model)
    out = await node({"messages": [HumanMessage(content="Find site")], "scout_gather_request": "Find site"}, {})
    assert len(model.calls) == 3
    assert model.bindings[-1] == {"finish_scout_report"}
    assert out["scout_evidence"][0]["status"] == "unstructured"


@pytest.mark.asyncio
async def test_rejected_reports_stop_after_two_attempts(monkeypatch):
    item = {"question": "Recipient", "status": "verified", "finding": "person@example.test", "source": "memory", "source_call_id": "invented"}
    model = Model([call("finish_scout_report", {"items": [item]}), call("finish_scout_report", {"items": [item]})])
    node = setup_node(monkeypatch, model)
    out = await node({"messages": [HumanMessage(content="Find recipient")], "scout_gather_request": "Find recipient email"}, {})
    assert len(model.calls) == 2
    assert out["scout_evidence"][0]["status"] == "report_failed"
    assert "two attempts" in out["gather_brief"]
