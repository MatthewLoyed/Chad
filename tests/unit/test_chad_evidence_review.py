import asyncio
from types import SimpleNamespace
import pytest
from evals.chad_evidence_review import review_evidence, review_messages, bounded_review_messages, canonical_tool_evidence, check_review_references
import json


def test_trace_is_data_not_execution_prompt():
    messages = review_messages("DO ACTION", "expected", {"tool_calls": [{"name": "read_file"}]})
    assert messages[0]["role"] == "system" and "DATA" in messages[0]["content"]
    assert '"id": "call-0000"' in messages[1]["content"]
    assert "event_index" not in messages[1]["content"]


def test_review_orders_eligibility_before_behavior_and_triage():
    system = review_messages("task", "expected", {})[0]["content"]
    assert system.index("1. Evidence eligibility") < system.index("2. Behavioral findings") < system.index("3. Triage")
    assert "independently evidenced behavioral errors" in system
    assert "do not recommend weakening safeguards" in system
    assert "Never narrate calls from separate cases as one execution sequence" in system
    assert "Do not invent tool names" in system


def test_context_separates_architecture_case_evidence_and_execution():
    context = {"cases": [{"label": "before", "call_ids": ["call-0000"],
                           "stop_boundary": "first decision only"}]}
    messages, _ = bounded_review_messages("task", "expected", {
        "tool_calls": [{"name": "delegate", "params": {"task": "x" * 500 + " decisive objective"}}]},
        review_context=context)
    data = json.loads(messages[1]["content"])
    assert data["review_context"] == context
    assert "not file bodies" in data["harness_context"]["retrieval"]
    assert "subset" in data["harness_context"]["selection"]
    assert "not a generic" in data["harness_context"]["execution"]
    assert "decisive objective" in data["captured_execution"]["tool_evidence"]["calls"][0]["params_excerpt"]


def test_context_is_budgeted_without_silent_truncation():
    with pytest.raises(ValueError, match="budget"):
        bounded_review_messages("task", "expected", {}, review_context={"input": "word " * 20000})


def test_large_trace_budget_preserves_all_call_and_result_indices():
    trace = {"tool_calls": [{"name": "read_file", "params": {"file_path": "x" * 3000}} for _ in range(110)],
             "events": [{"type": "tool_end", "name": "read_file", "result": "Unicode é " * 10000} for _ in range(110)]}
    messages, tokens = bounded_review_messages("task", "expected", trace)
    evidence = json.loads(messages[1]["content"])["captured_execution"]
    assert tokens <= 14000
    tools = evidence["tool_evidence"]
    assert len(tools["calls"]) == len(tools["unattributed_results"]) == 110
    assert tools["calls"][-1]["id"] == "call-0109"
    assert tools["unattributed_results"][-1]["id"] == "result-0109"


def test_nested_tools_match_own_call_not_event_position():
    calls = [{"name": "delegate", "params": {}}, {"name": "read_file", "params": {"path": "x"}}]
    trace = {"tool_calls": calls, "events": [
        {"type": "thinking", "text": "preface"},
        {"type": "tool_start", **calls[0]}, {"type": "tool_start", **calls[1]},
        {"type": "tool_end", "name": "read_file", "result": "file result"},
        {"type": "tool_end", "name": "delegate", "result": "parent result"}]}
    tools = canonical_tool_evidence(trace, 400)
    assert tools["calls"][1]["result_excerpt"] == "file result"
    assert tools["calls"][0]["result_excerpt"] == "parent result"
    assert not tools["unattributed_results"]
    assert check_review_references('[call-0001] (`read_file`) failed.', tools)["passed"]
    assert not check_review_references('[call-0001] (`delegate`) failed.', tools)["passed"]
    assert not check_review_references('[call-0125] failed.', tools)["passed"]
    assert not check_review_references('Index 3 failed.', tools)["passed"]
    mixed = check_review_references('[call-0001] (`read_file`); [round-1] timed out.', tools)
    assert mixed["tool_references_passed"] and not mixed["passed"]
    assert mixed["unregistered_harness_ids"] == ["round-1"]


def test_overlapping_same_name_results_remain_ambiguous():
    calls = [{"name": "read_file", "params": {"path": p}} for p in ("a", "b", "c")]
    trace = {"tool_calls": calls, "events": [
        {"type": "tool_start", **calls[0]}, {"type": "tool_start", **calls[1]},
        {"type": "tool_end", "name": "read_file", "result": "unknown first"},
        {"type": "tool_end", "name": "read_file", "result": "unknown second"},
        {"type": "tool_start", **calls[2]}, {"type": "tool_end", "name": "read_file", "result": "known c"}]}
    tools = canonical_tool_evidence(trace, 400)
    assert [c["result_status"] for c in tools["calls"]] == ["ambiguous", "ambiguous", "captured"]
    assert len(tools["unattributed_results"]) == 2
    assert tools["calls"][2]["result_excerpt"] == "known c"


def test_misaligned_starts_and_missing_end_do_not_invent_matches():
    trace = {"tool_calls": [{"name": "actual", "params": {}}], "events": [
        {"type": "tool_start", "name": "different", "params": {}},
        {"type": "tool_end", "name": "actual", "result": "unmatched"}]}
    tools = canonical_tool_evidence(trace, 400)
    assert not tools["start_sequence_matches_call_table"]
    assert tools["calls"][0]["result_status"] == "not_captured"
    assert tools["unattributed_results"][0]["attribution"] == "unmatched"


@pytest.mark.asyncio
async def test_explicit_qwen_and_no_tools():
    class Adapter:
        model_name = "qwen/test"
        async def generate(self, **kwargs):
            assert kwargs["tools"] == []
            assert kwargs["thinking_level"] == "off"
            return SimpleNamespace(text="diagnosis", tool_calls=[], finish_reason="stop", reasoning=None)
    result = await review_evidence("task", "expected", {}, model="qwen/test", adapter=Adapter())
    assert result["status"] == "completed"
    with pytest.raises(ValueError):
        await review_evidence("task", "expected", {}, model="lfm", adapter=Adapter())


@pytest.mark.asyncio
async def test_idle_cancels_generation_and_retains_partial():
    cancelled = []
    class Adapter:
        async def generate(self, **kwargs):
            kwargs["on_token"]("partial")
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
    result = await review_evidence("task", "expected", {}, model="qwen/test", adapter=Adapter(), idle_s=.02)
    assert result["status"] == "incomplete" and result["text"] == "partial"
    assert cancelled


@pytest.mark.asyncio
async def test_emitted_tool_request_cannot_dispatch():
    class Adapter:
        async def generate(self, **kwargs):
            return SimpleNamespace(text="attempt", tool_calls=[{"name": "browser_click"}], finish_reason="tool_calls", reasoning=None)
    result = await review_evidence("task", "expected", {}, model="qwen/test", adapter=Adapter())
    assert result["status"] == "incomplete"
