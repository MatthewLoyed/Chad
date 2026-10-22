import json
import pytest

from evals.chad_decision_review import package_decisions
from evals.chad_evidence_review import canonical_tool_evidence


def fixtures(root):
    for capture, messages, name in (
        ("runtime-first-decision.json", "runtime-supervisor-messages.json", "delegate"),
        ("verified-cutoff-first-decision.json", "verified-supervisor-messages.json", "web_search"),
    ):
        call = {"name": name, "params": {}}
        (root / capture).write_text(json.dumps({"executed_prompt": "task", "scope": "first decision only",
            "intentional_stop_after_first_tool": True, "cancellation_confirmed": True,
            "trace": {"completed": False, "tool_calls": [call], "events": [{"type": "tool_start", **call}]}}))
        (root / messages).write_text(json.dumps([{"role": "system", "content": "persona"},
                                                {"role": "user", "content": "task\noptional context"}]))


def test_case_ranges_provenance_and_missing_results_are_preserved(tmp_path):
    fixtures(tmp_path)
    prompt, trace, context = package_decisions(tmp_path)
    assert prompt == "task"
    assert [c["call_ids"] for c in context["cases"]] == [["call-0000"], ["call-0001"]]
    assert len(context["source_sha256"]) == 4
    evidence = canonical_tool_evidence(trace, 400)
    assert evidence["start_sequence_matches_call_table"]
    assert all(c["result_status"] == "not_captured" for c in evidence["calls"])


@pytest.mark.parametrize("field,value", [("cancellation_confirmed", False), ("executed_prompt", "different")])
def test_invalid_pair_rejected(tmp_path, field, value):
    fixtures(tmp_path)
    path = tmp_path / "verified-cutoff-first-decision.json"
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        package_decisions(tmp_path)
