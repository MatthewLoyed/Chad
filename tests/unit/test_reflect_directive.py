"""Reflect-then-repair directive (src/core/tools/reflect.py)."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.core.tools import failure, reflect


def _failed(tool: str, detail: str = "timed out", call_id: str = "c") -> ToolMessage:
    payload = failure.failure(detail, tool_name=tool)
    return ToolMessage(
        content="[TOOL_DATA]\n" + json.dumps(payload), tool_call_id=call_id, name=tool
    )


def _ok(tool: str, call_id: str = "c") -> ToolMessage:
    return ToolMessage(content="[TOOL_DATA]\nall good", tool_call_id=call_id, name=tool)


def test_no_directive_on_a_first_failure():
    """The failure envelope's own NEXT_ACTIONS is enough the first time."""
    assert reflect.build_reflection_directive([_failed("web_search")]) is None


def test_directive_on_a_second_consecutive_failure():
    msgs = [_failed("web_search", "timed out", "a"), _failed("web_search", "timed out", "b")]
    text = reflect.build_reflection_directive(msgs)
    assert text is not None
    assert "DIAGNOSIS:" in text and "CHANGING:" in text
    assert "web_search" in text


def test_directive_rejects_a_non_change_up_front():
    msgs = [_failed("web_search", "a", "1"), _failed("web_search", "b", "2")]
    text = reflect.build_reflection_directive(msgs)
    assert "'CHANGING: trying again' is not a change" in text


def test_a_success_breaks_the_streak():
    """Partial progress means the model is working, not stuck."""
    msgs = [_failed("web_search", "x", "a"), _ok("web_search", "b"), _failed("web_search", "y", "c")]
    assert reflect.build_reflection_directive(msgs) is None


def test_different_tools_failing_is_not_a_streak():
    """Trying another tool after a failure is correct recovery, not a loop."""
    msgs = [_failed("web_search", "x", "a"), _failed("fetch_url", "y", "b")]
    assert reflect.build_reflection_directive(msgs) is None


def test_streak_counts_only_the_most_recent_run():
    msgs = [_failed("web_search", "x", f"c{i}") for i in range(4)]
    text = reflect.build_reflection_directive(msgs)
    assert "failed 4 times" in text


def test_no_directive_without_tool_messages():
    assert reflect.build_reflection_directive([HumanMessage(content="hi")]) is None
    assert reflect.build_reflection_directive([]) is None


def test_ai_messages_between_failures_do_not_break_the_streak():
    """The model reasoning between two failed calls is normal, not progress."""
    msgs = [
        _failed("web_search", "x", "a"),
        AIMessage(content="let me try that again"),
        _failed("web_search", "y", "b"),
    ]
    assert reflect.build_reflection_directive(msgs) is not None
