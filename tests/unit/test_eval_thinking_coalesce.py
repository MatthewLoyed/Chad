"""Eval harness must coalesce streamed thinking deltas (one WS frame per token)."""

from __future__ import annotations

from evals.core import _render_events_chronological
from evals.live import LiveTrace, _record_thinking


def test_record_thinking_coalesces_same_agent_deltas():
    trace = LiveTrace(prompt="x", category="planning")
    reasoning: list[str] = []
    for tok in ["I", " need", " to", " gather", " info"]:
        _record_thinking(trace, reasoning, agent="Scout", text=tok)

    assert len(trace.events) == 1
    assert trace.events[0]["type"] == "thinking"
    assert trace.events[0]["agent"] == "Scout"
    assert trace.events[0]["text"] == "I need to gather info"
    assert len(trace.reasoning_chunks) == 1
    assert trace.reasoning_chunks[0]["text"] == "I need to gather info"
    assert "".join(reasoning) == "I need to gather info"
    # timeline stamped once per block, not per token
    assert trace.timeline == ["reasoning:Scout"]


def test_record_thinking_splits_on_agent_change_and_tool_boundary():
    trace = LiveTrace(prompt="x", category="planning")
    reasoning: list[str] = []
    _record_thinking(trace, reasoning, agent="Scout", text="scout thoughts ")
    _record_thinking(trace, reasoning, agent="Scout", text="continued")
    # tool boundary breaks coalescing
    trace.events.append({"type": "tool_start", "name": "google_drive_search", "params": {}})
    _record_thinking(trace, reasoning, agent="Scout", text="after tool")
    _record_thinking(trace, reasoning, agent="Main", text="supervisor ")
    _record_thinking(trace, reasoning, agent="Main", text="thinks")

    think = [e for e in trace.events if e.get("type") == "thinking"]
    assert len(think) == 3
    assert think[0]["text"] == "scout thoughts continued"
    assert think[1]["text"] == "after tool"
    assert think[2]["agent"] == "Main"
    assert think[2]["text"] == "supervisor thinks"


def test_record_thinking_append_false_starts_new_block():
    trace = LiveTrace(prompt="x", category="agent")
    reasoning: list[str] = []
    _record_thinking(trace, reasoning, agent="Main", text="first", append=True)
    _record_thinking(trace, reasoning, agent="Main", text="SECOND", append=False)
    assert len(trace.events) == 2
    assert trace.events[0]["text"] == "first"
    assert trace.events[1]["text"] == "SECOND"


def test_render_coalesces_legacy_fragmented_thinking_events():
    events = [
        {"type": "thinking", "agent": "Scout", "text": "I"},
        {"type": "thinking", "agent": "Scout", "text": " need"},
        {"type": "thinking", "agent": "Scout", "text": " info"},
        {"type": "tool_start", "name": "web_search", "params": {"q": "x"}},
        {"type": "tool_end", "name": "web_search", "result": "ok"},
    ]
    md = "\n".join(_render_events_chronological(events))
    assert md.count("### Thinking (Scout)") == 1
    assert "I need info" in md
    assert "### Tool `web_search`" in md
