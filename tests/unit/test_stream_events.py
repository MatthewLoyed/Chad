r"""
Unit tests for the Python typed stream emitter (Phase 1 step 2).

These mirror the four Phase 0 golden fixtures' LOGICAL signal sequences (the same
ones the TS legacy-frame bridge + equivalence suite drive) and assert the Python
emitter produces an equivalent StreamEvent stream: monotonic seq, turn_id on every
event, reasoning coalescing, text-part lifecycle, plan replace semantics, and
response-only answer synthesis. This is the Python-side half of the cross-language
equivalence the streaming refactor depends on.

Run: $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_stream_events.py -q
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.remote.stream_events import StreamEventEmitter


# -- derive helpers (mirror streamReducer.ts reasoningText / answerText) ----

def _reasoning_text(events: List[Dict[str, Any]]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "reasoning_delta")


def _answer_text(events: List[Dict[str, Any]]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "text_delta")


def _types(events: List[Dict[str, Any]]) -> List[str]:
    return [e["type"] for e in events]


def _collect(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for g in groups:
        out.extend(g)
    return out


# -- invariants -------------------------------------------------------------

def test_seq_is_monotonic_and_turn_id_present():
    em = StreamEventEmitter()
    events = _collect(
        em.run_started("turn_1"),
        em.reasoning_delta("Hello"),
        em.text_delta("Hi"),
        em.run_finished("Hi"),
    )
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert all(e["turn_id"] == "turn_1" for e in events)


def test_run_started_resets_sequence():
    em = StreamEventEmitter()
    em.run_started("turn_a")
    em.reasoning_delta("x")
    restart = em.run_started("turn_b")
    assert restart[0]["seq"] == 0
    assert restart[0]["turn_id"] == "turn_b"


# -- reasoning coalescing ---------------------------------------------------

def test_consecutive_reasoning_shares_one_part():
    em = StreamEventEmitter()
    em.run_started("t")
    a = em.reasoning_delta("Con")
    b = em.reasoning_delta("sider")
    assert a[0]["part_id"] == b[0]["part_id"]


def test_non_reasoning_event_closes_reasoning_part():
    em = StreamEventEmitter()
    em.run_started("t")
    first = em.reasoning_delta("I'll")
    tool = em.tool_start("tc1", "web_search")
    second = em.reasoning_delta("Found")
    # The tool_start must have emitted a reasoning_end before tool_start...
    assert _types(tool) == ["reasoning_end", "tool_start"]
    # ...and reasoning that resumes after the tool opens a NEW part.
    assert first[0]["part_id"] != second[0]["part_id"]


# -- subagent attribution ---------------------------------------------------

def test_reasoning_delta_carries_subagent_attribution():
    """agent_name + parent_id let the UI isolate a subagent's thoughts into a panel."""
    em = StreamEventEmitter()
    em.run_started("t")
    evt = em.reasoning_delta("sub thought", agent_name="Research Agent", parent_id="deleg1")[0]
    assert evt["agent_name"] == "Research Agent"
    assert evt["parent_id"] == "deleg1"


def test_reasoning_delta_omits_attribution_when_absent():
    em = StreamEventEmitter()
    em.run_started("t")
    evt = em.reasoning_delta("main thought")[0]
    assert "agent_name" not in evt
    assert "parent_id" not in evt


def test_tool_end_closes_open_reasoning_for_agent_handoff():
    """Scout → Planner: tool_end must end the Scout part so Planner opens a new one."""
    em = StreamEventEmitter()
    em.run_started("t")
    scout = em.reasoning_delta("scout facts", agent_name="Scout", parent_id="scout_1")
    end = em.tool_end("scout_1", ok=True, result={"gather_brief_ready": True})
    planner = em.reasoning_delta("now plan")
    assert _types(end) == ["reasoning_end", "tool_end"]
    assert scout[0]["part_id"] != planner[0]["part_id"]
    assert "parent_id" not in planner[0]


def test_attribution_change_closes_reasoning_without_tool_end():
    em = StreamEventEmitter()
    em.run_started("t")
    scout = em.reasoning_delta("scout facts", agent_name="Scout", parent_id="scout_1")
    handoff = em.reasoning_delta("planner brief")
    assert _types(handoff) == ["reasoning_end", "reasoning_delta"]
    assert scout[0]["part_id"] != handoff[1]["part_id"]
    assert "parent_id" not in handoff[1]


def test_same_attribution_keeps_coalescing():
    em = StreamEventEmitter()
    em.run_started("t")
    a = em.reasoning_delta("a", agent_name="Scout", parent_id="scout_1")
    b = em.reasoning_delta("b", agent_name="Scout", parent_id="scout_1")
    assert a[0]["part_id"] == b[0]["part_id"]
    assert _types(b) == ["reasoning_delta"]


def test_tool_start_carries_parent_id():
    em = StreamEventEmitter()
    em.run_started("t")
    evt = em.tool_start("sub-tool", "search_web", parent_id="deleg1")[-1]
    assert evt["type"] == "tool_start"
    assert evt["parent_id"] == "deleg1"


# -- fixture-equivalent sequences -------------------------------------------

def test_tool_use_fixture_sequence():
    """turn_tool_use: reasoning → tool → reasoning → captions → finish."""
    em = StreamEventEmitter()
    events = _collect(
        em.run_started("turn_77"),
        em.reasoning_delta("I'll"),
        em.reasoning_delta(" search"),
        em.reasoning_delta(" the web."),
        em.tool_start("tc1", "web_search", args={"query": "answer to everything"},
                      display_text="Searching the web..."),
        em.tool_end("tc1", ok=True, result="42"),
        em.reasoning_delta("Found"),
        em.reasoning_delta(" it."),
        em.text_delta("The answer "),
        em.text_delta("is 42."),
        em.token_usage({"total_tokens": 1240}),
        em.run_finished("The answer is 42."),
    )
    assert _reasoning_text(events) == "I'll search the web.Found it."
    assert _answer_text(events) == "The answer is 42."
    # Exactly one tool_start / tool_end pair.
    assert _types(events).count("tool_start") == 1
    assert _types(events).count("tool_end") == 1
    # Answer streamed via captions ⇒ no synthesized duplicate from run_finished.
    assert _types(events).count("text_end") == 1


def test_interleaved_speech_two_text_parts():
    """turn_interleaved_speech: caption deltas finalized by speech, twice."""
    em = StreamEventEmitter()
    events = _collect(
        em.run_started("turn_91"),
        em.reasoning_delta("Let me"),
        em.reasoning_delta(" answer in two parts."),
        em.text_delta("First, I'll explain. "),
        em.text_delta("It works like this. "),
        em.speech_finalize("First, I'll explain. It works like this. "),
        em.reasoning_delta("Now"),
        em.reasoning_delta(" reconsider the second half."),
        em.text_delta("Final answer here."),
        em.speech_finalize("Final answer here."),
        em.run_finished("First, I'll explain. It works like this. Final answer here."),
    )
    assert "reconsider the second half." in _reasoning_text(events)
    assert _answer_text(events) == "First, I'll explain. It works like this. Final answer here."
    # Two distinct text parts, each finalized once.
    text_part_ids = {e["part_id"] for e in events if e["type"] == "text_delta"}
    assert len(text_part_ids) == 2
    assert _types(events).count("text_end") == 2


def test_burst_caption_then_speech_not_doubled():
    """turn_reasoning_burst: a single caption then a speech of the same text."""
    em = StreamEventEmitter()
    events = _collect(
        em.run_started("turn_99"),
        em.reasoning_delta("Considering the request carefully before answering now."),
        em.text_delta("Done."),
        em.speech_finalize("Done."),
        em.run_finished("Done."),
    )
    assert _answer_text(events) == "Done."  # not "Done.Done."
    assert _types(events).count("text_end") == 1


def test_response_only_answer_is_synthesized():
    """turn_tokens_thinking: no captions/speech; answer arrives only in run_finished."""
    em = StreamEventEmitter()
    events = _collect(
        em.run_started("turn_42"),
        em.reasoning_delta("Let me check your inbox and turn on the lamp."),
        em.plan_snapshot([{"id": "s1", "description": "List unread emails", "status": "pending"}],
                         goal="Check inbox and turn on the lamp"),
        em.plan_snapshot([{"id": "s1", "description": "List unread emails", "status": "completed"}],
                         goal="Check inbox and turn on the lamp"),
        em.token_usage({"total_tokens": 1936}),
        em.run_finished("Your inbox has 3 unread emails and the lamp is on."),
    )
    assert _answer_text(events) == "Your inbox has 3 unread emails and the lamp is on."
    assert "lamp is on" in _answer_text(events)


def test_plan_snapshots_share_one_plan_id():
    """Replace semantics: successive PLAN snapshots collapse to one card."""
    em = StreamEventEmitter()
    em.run_started("turn_42")
    s1 = em.plan_snapshot([{"id": "s1", "status": "pending"}], goal="g")
    s2 = em.plan_snapshot([{"id": "s1", "status": "completed"}], goal="g")
    plan_ids = {e["plan_id"] for e in (s1 + s2) if e["type"] == "plan_snapshot"}
    assert plan_ids == {"turn_42-plan"}


def test_tools_snapshot_shape():
    em = StreamEventEmitter()
    em.run_started("turn_7")
    events = em.tools_snapshot(
        ["web_search", "read_file"],
        shortlist_size=12,
        selection_path="hybrid",
    )
    assert len(events) == 1
    snap = events[0]
    assert snap["type"] == "tools_snapshot"
    assert snap["menu_id"] == "turn_7-tools"
    assert snap["tools"] == ["web_search", "read_file"]
    assert snap["shortlist_size"] == 12
    assert snap["selection_path"] == "hybrid"


def test_run_error_shape():
    em = StreamEventEmitter()
    em.run_started("t")
    err = em.run_error("LM Studio is not running", code="lmstudio_offline")
    assert err[0]["type"] == "run_error"
    assert err[0]["message"] == "LM Studio is not running"
    assert err[0]["code"] == "lmstudio_offline"


def test_browser_progress_closes_reasoning_and_has_its_own_event_type():
    em = StreamEventEmitter()
    em.run_started('browser-turn')
    em.reasoning_delta('Public summary')
    progress = {'run_id': 'run', 'status': 'running', 'milestone': 'Locate response',
                'subgoal': 'Search conversation', 'reason': '', 'actions': 1, 'recoveries': 0}
    events = em.browser_progress(progress)
    assert _types(events) == ['reasoning_end', 'browser_progress']
    assert events[-1]['progress'] == progress
    assert events[-1]['turn_id'] == 'browser-turn'


def test_empty_deltas_emit_nothing():
    em = StreamEventEmitter()
    em.run_started("t")
    assert em.reasoning_delta("") == []
    assert em.text_delta("") == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
