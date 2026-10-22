"""Unit tests for scout gather-only tool guards and last-message brief capture."""
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.core.orchestration.nodes.scout_node import (
    _last_scout_message,
    _sanitize_scout_tool_call,
    filter_scout_handoff_tools,
)


def test_scout_blocks_write_file():
    _, err = _sanitize_scout_tool_call("write_file", {"path": "x.txt", "content": "hi"})
    assert err and "blocked" in err.lower()


def test_scout_blocks_all_delegates_when_disabled():
    with patch("src.shared.config_manager.ConfigManager.get", return_value=False):
        _, err = _sanitize_scout_tool_call(
            "delegate_to_traversal_agent",
            {"task": "Find wedding doc and return headings"},
        )
    assert err and "not available during scout" in err.lower()


def test_scout_blocks_code_agent_even_when_delegates_enabled():
    def _get(key, default=None):
        if key == "planner.scout_allow_delegates":
            return True
        if key == "planner.scout_delegate_allowlist":
            return ["traversal_agent", "research_agent"]
        return default

    with patch("src.shared.config_manager.ConfigManager.get", side_effect=_get):
        _, err = _sanitize_scout_tool_call(
            "delegate_to_code_agent",
            {"task": "Read README.md and return setup steps"},
        )
    assert err and "not available" in err.lower()


def test_scout_allows_traversal_delegate_when_in_allowlist():
    def _get(key, default=None):
        if key == "planner.scout_allow_delegates":
            return True
        if key == "planner.scout_delegate_allowlist":
            return ["traversal_agent", "research_agent"]
        return default

    with patch("src.shared.config_manager.ConfigManager.get", side_effect=_get):
        args, err = _sanitize_scout_tool_call(
            "delegate_to_traversal_agent",
            {"task": "Find wedding doc on Drive and return headings"},
        )
    assert err is None
    assert "[GATHER-ONLY" in args["task"]


def test_filter_scout_handoff_tools_excludes_code_agent_by_default():
    tools = [
        MagicMock(name="delegate_to_code_agent"),
        MagicMock(name="delegate_to_research_agent"),
    ]
    tools[0].name = "delegate_to_code_agent"
    tools[1].name = "delegate_to_research_agent"

    def _get(key, default=None):
        if key == "planner.scout_allow_delegates":
            return True
        if key == "planner.scout_delegate_allowlist":
            return ["research_agent"]
        return default

    with patch("src.shared.config_manager.ConfigManager.get", side_effect=_get):
        filtered = filter_scout_handoff_tools(tools)
    assert [t.name for t in filtered] == ["delegate_to_research_agent"]


def test_filter_scout_handoff_tools_empty_when_delegates_off():
    tools = [MagicMock(name="delegate_to_code_agent")]
    tools[0].name = "delegate_to_code_agent"
    with patch("src.shared.config_manager.ConfigManager.get", return_value=False):
        assert filter_scout_handoff_tools(tools) == []


def test_last_scout_message_is_verbatim_final_text():
    report = (
        "Hey planner, here's what I found:\n"
        "- File: Alex & Sam's Wedding Roadmap (id 1oQhbd…)\n"
        "- Date: April 11, 2028 at Cedar Hall\n"
    )
    msgs = [
        SystemMessage(content="scout"),
        AIMessage(
            content="",
            tool_calls=[{"id": "1", "name": "google_drive_search", "args": {"query": "x"}}],
        ),
        ToolMessage(content="Found 1 items", tool_call_id="1", name="google_drive_search"),
        AIMessage(content=report),
    ]
    assert _last_scout_message(msgs) == report.strip()


def test_last_scout_message_skips_tool_call_turns():
    msgs = [
        AIMessage(
            content="searching",
            tool_calls=[{"id": "1", "name": "web_search", "args": {}}],
        ),
        ToolMessage(content="ok", tool_call_id="1", name="web_search"),
    ]
    assert _last_scout_message(msgs) == ""
