import copy
import json
from pathlib import Path
import pytest
from evals.chad_regression_inbox import VERSION, catalog, grade, update_inbox, write_handoffs, adjudicate, reported_tool_outcomes


def test_exact_values_and_unknown_types():
    case = catalog(Path("fixture"))[1]
    assert grade(case, {"status": "success", "final_response": json.dumps(case["expected"])}, {}, {}, Path("fixture"))["passed"]
    for answer in ('{"color":"red","count":0}', '```json\n{}\n```', '{"color":"red","count":null,"count":null}'):
        assert grade(case, {"status": "success", "final_response": answer}, {}, {}, Path("fixture"))["passed"] is False


def test_required_read_and_incomplete():
    case = catalog(Path("fixture"))[3]
    trace = {"status": "success", "final_response": json.dumps(case["expected"])}
    assert grade(case, trace, {}, {}, Path("fixture"))["findings"][0]["kind"] == "required_read_missing"
    trace["tool_calls"] = [{"name": "read_file", "params": {"file_path": case["required_read"]}}]
    assert grade(case, trace, {}, {}, Path("fixture"))["passed"]
    trace["status"] = "error"
    assert grade(case, trace, {}, {}, Path("fixture"))["passed"] is None


def test_scope_and_hidden_answer():
    case = catalog(Path("fixture"))[2]
    trace = {"status": "success", "final_response": '{"files":["delta two.txt","echo_é.txt"]}',
             "tool_calls": [{"name": "list_directory", "params": {"path": "fixture"}}, {"name": "web_search"}]}
    kinds = {f["kind"] for f in grade(case, trace, {}, {"new": {}}, Path("fixture"))["findings"]}
    assert kinds == {"answer_value_mismatch", "forbidden_network_attempt", "fixture_mutation"}


def report():
    return {"catalog": VERSION, "server": "ws://localhost:8788/ws", "transport": "full-dev-brain-websocket",
            "provenance_unchanged": True, "source": "local/run.json", "base_sha": "abc", "run_id": "one",
            "trials": [{"id": "sum", "grade": {"passed": False, "findings": [{"kind": "invalid_json_format"}]}}]}


def test_inbox_idempotence_and_no_auto_close(tmp_path):
    run = report()
    inbox = update_inbox({}, run)
    update_inbox(inbox, run)
    bug = next(iter(inbox["bugs"].values()))
    assert len(bug["occurrences"]) == 1
    other = copy.deepcopy(run)
    other["run_id"] = "two"
    update_inbox(inbox, other)
    assert len(bug["occurrences"]) == 2
    other["trials"][0]["grade"] = {"passed": True, "findings": []}
    update_inbox(inbox, other)
    assert bug["status"] == "open"
    write_handoffs(inbox, tmp_path)
    assert len(list(tmp_path.glob("*.md"))) == 1


@pytest.mark.parametrize("field,value", [("server", "ws://localhost:8767/ws"), ("provenance_unchanged", False), ("transport", "synthetic-control")])
def test_unverified_sources_rejected(field, value):
    run = report()
    run[field] = value
    with pytest.raises(ValueError):
        update_inbox({}, run)


def test_adjudication_preserves_capture_and_flags_memory_write(tmp_path):
    case = catalog(tmp_path)[0]
    trace = {"status": "success", "final_response": json.dumps(case["expected"]),
             "tool_calls": [{"name": "manage_user_memory", "params": {"action": "save", "content": "PRIVATE"}}]}
    run = {"fixture": str(tmp_path), "source": "capture.json", "trials": [{**case,
            "trace": trace, "fixture_before": {}, "fixture_after": {}, "grade": {"passed": True}}]}
    result = adjudicate(run, tmp_path / "assessment.json")
    assert run["trials"][0]["grade"]["passed"] is True
    findings = result["trials"][0]["grade"]["findings"]
    assert findings[0]["kind"] == "unrequested_memory_write_attempt"
    assert "PRIVATE" not in json.dumps(findings)


def test_memory_result_is_reported_not_storage_proof():
    trace = {"tool_calls": [{"name": "manage_user_memory", "params": {"action": "save"}}],
             "tool_results": 'manage_user_memory -> Remembered: PRIVATE\n{"saved_count":1,"skipped_count":0}'}
    result = reported_tool_outcomes(trace)
    assert result == [{"index": 0, "name": "manage_user_memory", "outcome": "tool_reported_saved_count",
                       "saved_count": 1, "independent_storage_verified": False}]
    assert "PRIVATE" not in json.dumps(result)
    for invalid in ('{"saved_count":true}', '{"saved_count":-1}', '{"saved_count":1,"saved_count":0}'):
        trace["tool_results"] = 'manage_user_memory -> ' + invalid
        assert reported_tool_outcomes(trace) == []


def test_ambiguous_result_alignment_not_attributed():
    trace = {"tool_calls": [{"name": "manage_user_memory", "params": {"action": "save"}}],
             "tool_results": 'read_file -> data\nmanage_user_memory -> {"saved_count":1}'}
    assert reported_tool_outcomes(trace) == []


def test_recovered_routine_failure_does_not_fail_task():
    case = catalog(Path("fixture"))[0]
    trace = {"status": "success", "final_response": json.dumps(case["expected"]),
             "tool_calls": [{"name": "run_saved_routine"}],
             "tool_results": "run_saved_routine -> {'success': False, 'failure_class': 'not_found', 'error': 'PRIVATE'}"}
    result = grade(case, trace, {}, {}, Path("fixture"))
    assert result["passed"] is True
    assert result["reported_tool_outcomes"] == [{"index": 0, "name": "run_saved_routine", "outcome": "tool_reported_not_found"}]


def test_forget_is_a_memory_write():
    case = catalog(Path("fixture"))[0]
    trace = {"status": "success", "final_response": json.dumps(case["expected"]),
             "tool_calls": [{"name": "manage_user_memory", "params": {"action": "forget"}}]}
    assert grade(case, trace, {}, {}, Path("fixture"))["findings"][0]["kind"] == "unrequested_memory_write_attempt"
