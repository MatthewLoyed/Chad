import copy
import json

import pytest

from evals.chad_bug_findings import identify_listing_trial
from evals.chad_bug_findings import summarize_findings
from evals.chad_discovery_validation import control_trials, discovery_prompt, assess_discovery, render_validation_handoff


def oracle():
    return {"schema_version": 1, "cases": [
        {"case_id": "case-a", "verdict": "no_failure", "findings": [], "hypotheses": []},
        {"case_id": "case-b", "verdict": "failure", "findings": [
            {"kind": "answer_contract_violation", "evidence": {"kind": "response_not_json"}}], "hypotheses": []},
        {"case_id": "case-c", "verdict": "failure", "findings": [
            {"kind": "forbidden_network_tool_attempt", "evidence": {"kind": "tool_call", "index": 1, "name": "web_search"}}], "hypotheses": []},
        {"case_id": "case-d", "verdict": "inconclusive", "findings": [], "hypotheses": []},
    ]}


def trace(value):
    return {"status": "success", "error": None, "final_response": json.dumps(value), "tool_calls": []}


def test_known_controls_cover_good_format_network_and_infrastructure():
    cases = control_trials()
    assert [identify_listing_trial(case)["status"] for case in cases] == [
        "passed_contract", "failed_contract", "failed_contract", "inconclusive"]
    result = assess_discovery(cases, trace(oracle()))
    assert result["passed"] and len(result["cases"]) == 4
    assert not result["repair_dispatched"] and not result["hypotheses_verified"]


def test_neutral_prompt_allows_no_failure_and_hides_independent_grades():
    prompt = discovery_prompt(control_trials())
    assert "Do not assume a failure" in prompt
    assert "no_failure" in prompt and "inconclusive" in prompt
    assert "Diagnose this eval failure" not in prompt
    assert "expected_verdict" not in prompt and "failed_contract" not in prompt
    assert "fixture_evidence" in prompt and "tool_results" in prompt


def test_false_positive_on_known_good_is_rejected():
    value = oracle()
    value["cases"][0].update(verdict="failure", findings=copy.deepcopy(value["cases"][1]["findings"]))
    result = assess_discovery(control_trials(), trace(value))
    assert not result["passed"]
    assert result["cases"][0]["false_positive_kinds"] == ["answer_contract_violation"]


def test_correct_answer_does_not_hide_missed_network_failure():
    value = oracle()
    value["cases"][2].update(verdict="no_failure", findings=[])
    result = assess_discovery(control_trials(), trace(value))
    assert not result["passed"]
    assert result["cases"][2]["missed_finding_kinds"] == ["forbidden_network_tool_attempt"]


@pytest.mark.parametrize("index,name", [(0, "web_search"), (99, "web_search"), (True, "web_search"),
                                        (1, "list_directory"), (-1, "web_search")])
def test_fabricated_or_invalid_tool_reference_is_rejected(index, name):
    value = oracle()
    value["cases"][2]["findings"][0]["evidence"].update(index=index, name=name)
    result = assess_discovery(control_trials(), trace(value))
    assert not result["passed"] and result["cases"][2]["evidence_errors"]


def test_infrastructure_failure_is_not_a_behavior_bug():
    value = oracle()
    value["cases"][3].update(verdict="failure", findings=copy.deepcopy(value["cases"][1]["findings"]))
    result = assess_discovery(control_trials(), trace(value))
    assert not result["passed"] and result["cases"][3]["expected_verdict"] == "inconclusive"


def test_recovered_tool_error_does_not_make_a_completed_turn_inconclusive():
    cases = control_trials()
    cases[0]['trace']['tool_results'] += '\nAn intermediate optional tool returned not_found; the final task completed.'
    result = assess_discovery(cases, trace(oracle()))
    assert result['passed'] and result['cases'][0]['expected_verdict'] == 'no_failure'


@pytest.mark.parametrize("mutate", [
    lambda value: value["cases"].pop(),
    lambda value: value["cases"].append(copy.deepcopy(value["cases"][0])),
    lambda value: value["cases"][0].update(case_id="invented"),
    lambda value: value.update(schema_version=True),
    lambda value: value["cases"][0].update(extra="not allowed"),
    lambda value: value["cases"][1].update(hypotheses=[{"text": "Maybe", "finding_indices": [99]}]),
])
def test_schema_omissions_duplicates_and_unsupported_hypotheses_fail_closed(mutate):
    value = oracle()
    mutate(value)
    assert not assess_discovery(control_trials(), trace(value))["passed"]


def test_review_cannot_use_tools_even_with_correct_verdicts():
    review = trace(oracle())
    review["tool_calls"] = [{"name": "web_search"}]
    result = assess_discovery(control_trials(), review)
    assert not result["passed"] and "no-tools" in result["errors"][0]


def test_empty_suite_cannot_be_called_validated():
    result = assess_discovery([], trace({"schema_version": 1, "cases": []}))
    assert not result["passed"] and result["errors"]


@pytest.mark.parametrize("content", ['```json\n{}\n```', '{"schema_version":1,"schema_version":1,"cases":[]}',
                                     '{"schema_version":NaN,"cases":[]}'])
def test_markdown_duplicate_keys_and_nonstandard_constants_fail(content):
    result = assess_discovery(control_trials(), {"status": "success", "final_response": content})
    assert not result["passed"] and result["errors"]


def test_incomplete_reviewer_is_not_scored_as_behavioral_misclassification():
    result = assess_discovery(control_trials(), {"status": "error", "error": "timeout"})
    assert not result["passed"] and not result["cases"]
    assert result["errors"] == ['Reviewer did not complete']


def test_json_value_mismatch_requires_exact_response_not_false_non_json_claim():
    cases = control_trials()
    cases[1]["trace"]["final_response"] = '{"files":["wrong.txt"]}'
    value = oracle()
    assert not assess_discovery(cases, trace(value))["passed"]
    value["cases"][1]["findings"][0]["evidence"] = {
        "kind": "response_equals", "value": cases[1]["trace"]["final_response"]}
    assert assess_discovery(cases, trace(value))["passed"]
    value["cases"][1]["findings"][0]["evidence"] = {"kind": "response_value_mismatch"}
    assert assess_discovery(cases, trace(value))["passed"]


def test_value_mismatch_reference_does_not_validate_malformed_json():
    value = oracle()
    value['cases'][1]['findings'][0]['evidence'] = {'kind': 'response_value_mismatch'}
    assert not assess_discovery(control_trials(), trace(value))['passed']


@pytest.mark.parametrize('rewritten', [{'files': ['harbor.txt', 'ridge.txt']},
                                      '{"files": ["harbor.txt", "ridge.txt"]}'])
def test_corrected_expected_answer_cannot_be_cited_as_actual_response(rewritten):
    cases = control_trials()
    cases[1]['trace']['final_response'] = '{"files":["wrong.txt"]}'
    value = oracle()
    value['cases'][1]['findings'][0]['evidence'] = {'kind': 'response_equals', 'value': rewritten}
    result = assess_discovery(cases, trace(value))
    assert not result['passed'] and result['cases'][1]['evidence_errors']


def test_flattening_a_tool_event_into_a_finding_does_not_pass_the_protocol():
    value = oracle()
    value['cases'][2]['findings'] = [{'kind': 'tool_call', 'index': 1, 'name': 'web_search'}]
    result = assess_discovery(control_trials(), trace(value))
    assert not result['passed'] and 'Invalid finding shape' in result['errors']


def test_regular_file_answer_can_exclude_unchanged_subdirectory():
    value = control_trials()[0]
    value["fixture_before"]["nested"] = {"type": "directory_or_other", "child_manifest": {"child": {"sha256": "before"}}}
    value["fixture_after"]["nested"] = copy.deepcopy(value["fixture_before"]["nested"])
    assert identify_listing_trial(value)["status"] == "passed_contract"
    value["fixture_after"]["nested"]["child_manifest"]["child"]["sha256"] = "after"
    assert [f["kind"] for f in identify_listing_trial(value)["findings"]] == ["fixture_mutation"]


def test_formatting_and_wrong_filename_findings_are_not_merged_as_one_symptom():
    formatted = control_trials()[1]
    wrong_names = copy.deepcopy(formatted)
    wrong_names['id'] = 'wrong-filenames'
    wrong_names['trace']['final_response'] = '{"files":["wrong.txt"]}'
    grouped = summarize_findings([formatted, wrong_names])
    assert len(grouped['findings']) == 2
    assert {group['subtype'] for group in grouped['findings']} == {'invalid_json_format', 'json_answer_mismatch'}


def test_handoff_keeps_private_raw_model_data_out_of_cloud_brief():
    heldout = control_trials()[1]
    heldout['trace']['final_response'] = 'PRIVATE_RESPONSE'
    heldout['trace']['tool_results'] = 'PRIVATE_RESULTS'
    heldout['grade'] = identify_listing_trial(heldout)
    report = {'heldout': heldout, 'assessment': assess_discovery(control_trials(), trace(oracle())),
              'persona_baseline': 'PRIVATE_OVERLAY', 'review_trace': {'reasoning_full': 'PRIVATE_REASONING'}}
    brief = render_validation_handoff(report)
    assert 'answer_contract_violation' in brief and '4 of 4' in brief
    assert not any(secret in brief for secret in ('PRIVATE_RESPONSE', 'PRIVATE_RESULTS', 'PRIVATE_OVERLAY', 'PRIVATE_REASONING'))


@pytest.fixture
def validation_stub(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from evals import live
    from evals import chad_discovery_validation as validation
    import evals.preflight.gate as preflight
    (tmp_path / 'evals').mkdir()
    script = tmp_path / 'evals/probe.py'
    script.write_text('synthetic harness')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/config.yaml').write_text('synthetic: true')
    monkeypatch.setattr(validation, '__file__', str(script))
    monkeypatch.setattr(live, 'SERVER_WS', 'ws://localhost:8788/ws')
    monkeypatch.setattr(validation, 'resident_models', lambda: [])
    monkeypatch.setattr(validation, 'snapshot_persona', lambda *args: {'sha256': 'persona'})
    monkeypatch.setattr(validation.subprocess, 'check_output', lambda *args, **kwargs: 'sha')
    clear = SimpleNamespace(ok=True, manifest=lambda: {'checks': {'server': {'status': 'ok'}}})
    monkeypatch.setattr(preflight, 'run_preflight', lambda *args, **kwargs: clear)
    return validation, live, clear


@pytest.mark.asyncio
async def test_validation_runs_exactly_two_turns_and_persists_handoff(validation_stub, tmp_path, monkeypatch):
    from pathlib import Path
    from evals.live import LiveTrace
    validation, live, _ = validation_stub
    calls = []
    async def turn(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            folder = prompt.split('local folder: ', 1)[1].split('. Include hidden', 1)[0]
            answer = json.dumps({'files': sorted(path.name for path in Path(folder).iterdir() if path.is_file())})
            return LiveTrace(prompt=prompt, category='agent', status='success', final_response=answer,
                             tool_calls=[{'name': 'list_directory', 'params': {'path': folder, 'include_hidden': True}}])
        value = oracle()
        value['cases'].append({'case_id': 'case-e', 'verdict': 'no_failure', 'findings': [], 'hypotheses': []})
        return LiveTrace(prompt=prompt, category='agent', status='success', final_response=json.dumps(value))
    monkeypatch.setattr(live, 'probe_live', turn)
    output = tmp_path / 'report.json'
    result = await validation.run_validation(output)
    assert len(calls) == 2 and result['decision'] == 'sample_validated'
    assert result['provenance_unchanged'] and not result['repair_dispatched']
    assert output.with_suffix('.handoff.md').is_file()


@pytest.mark.asyncio
async def test_incomplete_heldout_turn_does_not_launch_reviewer(validation_stub, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from evals.live import LiveTrace
    validation, live, _ = validation_stub
    turn = AsyncMock(return_value=LiveTrace(prompt='heldout', category='agent', status='error', error='timeout'))
    monkeypatch.setattr(live, 'probe_live', turn)
    result = await validation.run_validation(tmp_path / 'report.json')
    assert turn.await_count == 1 and result['decision'] == 'inconclusive'
    assert 'review_trace' not in result


@pytest.mark.asyncio
async def test_preflight_blocks_validation_without_inference(validation_stub, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    validation, live, preflight = validation_stub
    preflight.ok = False
    turn = AsyncMock()
    monkeypatch.setattr(live, 'probe_live', turn)
    result = await validation.run_validation(tmp_path / 'report.json')
    turn.assert_not_awaited()
    assert result['decision'] == 'inconclusive' and 'Preflight' in result['error']


@pytest.mark.asyncio
async def test_reviewer_infrastructure_error_is_inconclusive_not_a_wrong_verdict(validation_stub, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from evals.live import LiveTrace
    validation, live, _ = validation_stub
    turn = AsyncMock(side_effect=[
        LiveTrace(prompt='heldout', category='agent', status='success', final_response='{}'),
        LiveTrace(prompt='review', category='agent', status='error', error='timeout'),
    ])
    monkeypatch.setattr(live, 'probe_live', turn)
    result = await validation.run_validation(tmp_path / 'report.json')
    assert turn.await_count == 2 and result['decision'] == 'inconclusive'
    assert not result['assessment']['cases']
