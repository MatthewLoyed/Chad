"""Scout receives a narrow task context and independent core tool bindings."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.core.orchestration.nodes import scout_node as scout


def test_core_binding_does_not_inherit_supervisor_execution_tools():
    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = lambda name: SimpleNamespace(name=name)
    tools, mapping = scout._bind_scout_tools(
        {"tools": [{"name": "open_app"}, {"name": "create_gmail_draft"}]}, registry, []
    )
    assert set(mapping) == set(scout._SCOUT_CORE_TOOLS)
    assert {t.name for t in tools} == set(mapping)


@pytest.mark.parametrize("action", ["save", "forget", None])
def test_memory_mutations_blocked(action):
    _, error = scout._sanitize_scout_tool_call("manage_user_memory", {"action": action})
    assert error


@pytest.mark.parametrize("action", ["search", "status"])
def test_memory_reads_allowed(action):
    _, error = scout._sanitize_scout_tool_call("manage_user_memory", {"action": action})
    assert error is None


@pytest.mark.asyncio
async def test_real_scout_node_isolates_context_and_filters_discovery(monkeypatch):
    from src.application.events.event_bus import EventBus
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    monkeypatch.setattr(EventBus, "get_instance", lambda: MagicMock())
    monkeypatch.setattr(SystemPromptBuilder, "build", lambda **kwargs: "Scout role only")
    monkeypatch.setattr(scout, "_maybe_unload_main_model", AsyncMock())
    seen = []
    bindings = []
    responses = iter([
        AIMessage(content="", tool_calls=[{"id": "discover", "name": "search_for_tool", "args": {}}]),
        AIMessage(content="Verified facts; remaining gaps explicit."),
    ])

    class Model:
        def copy(self, **kwargs):
            return self
        def bind_tools(self, tools):
            bindings.append([t.name for t in tools])
            return self
        async def ainvoke(self, messages, config=None):
            seen.append(list(messages))
            return next(responses)

    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = lambda name: SimpleNamespace(name=name, description="Read/search tool")
    registry.get_all_schemas.return_value = [{"name": "search_gmail"}, {"name": "web_search"}]
    execute = AsyncMock(return_value={"_injected_schemas": [
        {"name": "open_app"}, {"name": "create_gmail_draft"}, {"name": "search_contacts"},
    ]})
    monkeypatch.setattr(scout, "_execute_scout_tool", execute)
    result = await scout.make_scout_node(Model(), registry, [])({
        "messages": [HumanMessage(content="Original user task")],
        "scout_gather_request": "Find contact in personal memory",
        "tools": [{"name": "open_app"}, {"name": "search_contacts"}],
        "system_instruction": "UNRELATED_RETRIEVAL_AND_SUPERVISOR_CONTEXT",
    }, {})
    prompt = seen[0][0].content
    assert "Gmail / email retrieval: `search_gmail` (discover)" in prompt
    assert "Public web search: `web_search` (loaded)" in prompt
    assert "search_gmail" not in bindings[0]
    assert "Scout role only" in prompt
    assert "Find contact in personal memory" in prompt
    assert "Original user task" in prompt
    assert "UNRELATED_RETRIEVAL" not in prompt
    assert "open_app" not in prompt
    assert set(bindings[0]) == set(scout._SCOUT_CORE_TOOLS) | {"finish_scout_report"}
    assert "search_contacts" in bindings[-1]  # already in state, but not previously bound
    assert "open_app" not in bindings[-1]
    assert "create_gmail_draft" not in bindings[-1]
    assert result["gather_brief"] == "Verified facts; remaining gaps explicit."


def test_capability_summary_uses_registered_reads_only():
    registry = MagicMock()
    registry.get_all_schemas.return_value = [
        {"name": "send_gmail_message"},
        {"type": "function", "function": {"name": "search_gmail"}},
        {"name": "search_contacts"},
        {"name": "get_calendar_events"},
        {"name": "google_drive_search"},
        {"name": "manage_user_memory"},
        {"name": "ui_click"},
        {"name": "browser_snapshot"},
    ]
    summary = scout.build_scout_capability_summary(registry, {"manage_user_memory"})
    for expected in ["search_gmail", "search_contacts", "google_drive_search", "get_calendar_events", "browser_snapshot"]:
        assert f"`{expected}` (discover)" in summary
    assert "`manage_user_memory` (loaded)" in summary
    assert "send_gmail_message" not in summary
    assert "ui_click" not in summary
    assert "Local file" not in summary
    registry.get_langchain_tool_by_name.assert_not_called()


def test_write_only_service_does_not_advertise_retrieval():
    registry = MagicMock()
    registry.get_all_schemas.return_value = [{"name": "send_gmail_message"}]
    assert scout.build_scout_capability_summary(registry, set()) == "No additional retrieval capabilities are registered."
