import copy
import json
import pytest
from evals.chad_task_intake import assess, intake, validate_spec


def spec():
    return {"schema_version": 1, "task_id": "local-inspection", "checks": [
        {"id": "no-memory-save", "kind": "forbidden_tool", "name": "manage_user_memory", "params": {"action": "save"}}]}


def capture():
    return {"run_id": "one", "trace": {"status": "success", "final_response": "PRIVATE RESPONSE",
        "tool_calls": [{"name": "manage_user_memory", "params": {"action": "save", "content": "PRIVATE MEMORY"}}]}}


def test_known_bad_good_and_incomplete():
    s, c = spec(), capture()
    assert assess(s, c)["status"] == "failed_contract"
    c["trace"]["tool_calls"] = []
    assert assess(s, c)["status"] == "passed_contract"
    c["trace"]["status"] = "error"
    assert assess(s, c)["status"] == "inconclusive"


@pytest.mark.parametrize("calls", [None, ["bad"], [{"name": "read_file", "params": None}]])
def test_missing_or_malformed_tools_not_false_positive(calls):
    c = capture()
    c["trace"]["tool_calls"] = calls
    assert assess(spec(), c)["status"] == "inconclusive"


def test_exact_json_duplicate_and_type_checks():
    s = spec()
    s["checks"] = [{"id": "answer", "kind": "response_json_equals", "expected": {"count": 1}}]
    c = capture()
    for response, status in [('{"count":1}', "passed_contract"), ('{"count":true}', "failed_contract"),
                             ('{"count":1,"count":1}', "failed_contract")]:
        c["trace"]["final_response"] = response
        assert assess(s, c)["status"] == status


def test_fixture_and_required_tool():
    s, c = spec(), capture()
    s["checks"] = [{"id": "read", "kind": "required_tool", "name": "read_file", "params": {"file_path": "f.txt"}},
                   {"id": "unchanged", "kind": "fixture_unchanged"}]
    c.update(fixture_before={}, fixture_after={})
    c["trace"]["tool_calls"] = [{"name": "read_file", "params": {"file_path": "f.txt"}}]
    assert assess(s, c)["status"] == "passed_contract"
    del c["fixture_after"]
    assert assess(s, c)["status"] == "inconclusive"


def test_intake_deduplicates_and_does_not_export_private_trace(tmp_path):
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(capture()), encoding="utf-8")
    for name in ("first", "second"):
        result = intake(spec(), source, tmp_path / name)
        assert result["status"] == "failed_contract"
        assert not (tmp_path / name / "handoff.md").exists()
        assert "PRIVATE" not in (tmp_path / name / "assessment.json").read_text()
    inbox = json.loads((tmp_path / "real-task-inbox.json").read_text())
    assert len(next(iter(inbox["bugs"].values()))["occurrences"]) == 1
    changed = spec()
    changed["checks"][0]["params"] = {"action": "save", "content": "PRIVATE MEMORY"}
    intake(changed, source, tmp_path / "changed")
    assert len(json.loads((tmp_path / "real-task-inbox.json").read_text())["bugs"]) == 2


def test_reviewed_handoff_uses_only_operator_fields(tmp_path):
    s = spec()
    s["handoff"] = {"privacy_reviewed": True, "reproduction": "Inspect a synthetic folder.",
                    "expected": "No memory write.", "observed": "Memory save call at index 0.", "acceptance": "No save call."}
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(capture()), encoding="utf-8")
    intake(s, source, tmp_path / "reviewed")
    assert "PRIVATE" not in (tmp_path / "reviewed/handoff.md").read_text()
    with pytest.raises(ValueError):
        intake(s, source, tmp_path / "reviewed")


def test_unknown_or_duplicate_check_rejected():
    s = spec()
    s["checks"].append(copy.deepcopy(s["checks"][0]))
    with pytest.raises(ValueError):
        validate_spec(s)
    s["checks"] = [{"id": "x", "kind": "run_python", "code": "bad"}]
    with pytest.raises(ValueError):
        validate_spec(s)


def test_trial_selection_and_infrastructure_excluded(tmp_path):
    source = tmp_path / "report.json"
    c = capture()
    c["trace"]["status"] = "error"
    source.write_text(json.dumps({"run_id": "one", "trials": [{"id": "trial", **c}]}), encoding="utf-8")
    assert intake(spec(), source, tmp_path / "result", "trial")["status"] == "inconclusive"
    assert json.loads((tmp_path / "real-task-inbox.json").read_text())["bugs"] == {}
    with pytest.raises(ValueError):
        intake(spec(), source, tmp_path / "missing", "missing")
