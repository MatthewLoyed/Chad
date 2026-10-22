import copy
import json
from types import SimpleNamespace

import pytest

from evals.chad_bug_findings import (
    identify_listing_trial, summarize_findings, assess_self_review, render_handoff, self_review_prompt,
)
from evals.chad_triage_gate import SCHEMA
from evals.chad_identification_probe import require_dev_server, fixture_manifest, listing_prompt


def trial(identifier="listing-1"):
    return {"id": identifier, "folder": "C:/fixture", "expected": ["a.txt"],
            "fixture_before": {"a.txt": {"sha256": "same"}},
            "fixture_after": {"a.txt": {"sha256": "same"}},
            "trace": {"status": "success", "error": None,
                      "final_response": '{"files":["a.txt"]}',
                      "tool_calls": [{"name": "list_directory", "params": {"path": "C:/fixture"}}]}}


def test_known_good_is_not_a_bug():
    result = identify_listing_trial(trial())
    assert result["status"] == "passed_contract" and not result["findings"]
    assert not summarize_findings([trial()])["findings"]


@pytest.mark.parametrize("error,status", [("timeout", "success"), (None, "running"),
                                          (None, "error")])
def test_incomplete_turns_are_inconclusive_without_behavioral_findings(error, status):
    value = trial()
    value["trace"].update(error=error, status=status, final_response="bad")
    result = identify_listing_trial(value)
    assert result["status"] == "inconclusive" and not result["findings"]


def test_inflight_trial_survives_handoff_without_fixture_after():
    value = trial()
    value['trace'] = {'status': 'running'}
    del value['fixture_after']
    brief = render_handoff({'trials': [value], 'decision': 'inconclusive'})
    assert 'inconclusive' in brief and 'No behavioral failure identified' in brief


def test_completed_turn_without_independent_fixture_evidence_is_inconclusive():
    value = trial()
    del value['fixture_after']
    result = identify_listing_trial(value)
    assert result['status'] == 'inconclusive' and not result['findings']


def test_correct_answer_cannot_hide_forbidden_recorded_attempt():
    value = trial()
    value["trace"]["tool_calls"].append({"name": "web_search", "params": {"query": "irrelevant"}})
    result = identify_listing_trial(value)
    assert result["acceptance"]["correct_raw_json"]
    finding, = result["findings"]
    assert finding["kind"] == "forbidden_network_tool_attempt"
    assert finding["evidence"]["tool_call_index"] == 1
    assert "does not establish" in finding["evidence"]["scope"]


def test_formatting_and_fixture_failures_stay_separate():
    value = trial()
    value["trace"]["final_response"] = '```json\n{"files":["a.txt"]}\n```'
    value["fixture_after"]["a.txt"]["sha256"] = "changed"
    result = identify_listing_trial(value)
    assert {f["kind"] for f in result["findings"]} == {"answer_contract_violation", "fixture_mutation"}


def test_missing_listing_is_a_capture_limited_evidence_gap():
    value = trial()
    value["trace"]["tool_calls"] = []
    finding, = identify_listing_trial(value)["findings"]
    assert finding["kind"] == "required_listing_evidence_missing"
    assert finding["evidence"]["scope"] == "captured tool_calls only"


def test_repetition_counts_trials_instead_of_duplicate_calls():
    value = trial()
    value["trace"]["tool_calls"] += [{"name": "web_search"}, {"name": "web_search"}]
    finding, = summarize_findings([value])["findings"]
    assert len(finding["occurrences"]) == 2
    assert finding["reproducibility"] == "observed_once"
    second = copy.deepcopy(value)
    second["id"] = "listing-2"
    finding, = summarize_findings([value, second])["findings"]
    assert finding["reproducibility"] == "observed_in_multiple_trials"
    assert finding["cause_status"] == "unverified" and not finding["repair_dispatched"]


@pytest.mark.parametrize("url", ["ws://localhost:8767/ws", "ws://example.com:8788/ws",
                                 "ws://localhost:8788/other", "http://localhost:8788/ws"])
def test_identification_cannot_accidentally_run_against_production(url):
    with pytest.raises(ValueError, match="8788"):
        require_dev_server(url)


def test_local_fixture_manifest_and_prompt_do_not_disclose_expected_names(tmp_path):
    (tmp_path / "random.txt").write_bytes(b"synthetic")
    before = fixture_manifest(tmp_path)
    (tmp_path / "random.txt").write_bytes(b"mutated")
    assert fixture_manifest(tmp_path) != before
    require_dev_server("ws://localhost:8788/ws")
    assert "random.txt" not in listing_prompt(tmp_path)


def test_self_review_is_checked_against_trace_and_operator_failure():
    value = trial()
    value['trace']['final_response'] = '```json\n{"files":["a.txt"]}\n```'
    review = {'status': 'success', 'final_response': json.dumps(SCHEMA), 'tool_calls': []}
    result = assess_self_review(value, review)
    assert result['status'] == 'evidence_valid_review'
    assert result['supported_failure_evidence'] == ['answer_contract_violation']
    assert result['cause_status'] == 'unverified'


def test_self_review_receives_tool_results_and_independent_fixture_evidence():
    value = trial()
    value['prompt'] = 'Inspect the local folder and return raw JSON'
    value['trace']['tool_results'] = 'TOOL_RESULT_MARKER: a.txt'
    prompt = self_review_prompt(value)
    assert 'TOOL_RESULT_MARKER' in prompt
    assert 'expected_filenames' in prompt and 'fixture_evidence' in prompt
    assert 'before' in prompt and 'after' in prompt
    assert 'omitted/truncated result does not establish' in prompt


def test_schema_valid_but_irrelevant_review_is_not_a_success():
    value = trial()
    value['trace']['tool_calls'].append({'name': 'web_search'})
    proposal = copy.deepcopy(SCHEMA)
    proposal['observations'] = [{'id': 'o1', 'kind': 'tool_call', 'index': 0, 'name': 'list_directory'}]
    result = assess_self_review(value, {'status': 'success', 'final_response': json.dumps(proposal)})
    assert result['gate']['valid']
    assert result['status'] == 'review_needs_work'
    assert result['uncovered_finding_kinds'] == ['forbidden_network_tool_attempt']


def test_self_review_tool_calls_violate_analysis_scope_even_with_correct_facts():
    value = trial()
    value['trace']['final_response'] = 'not JSON'
    result = assess_self_review(value, {'status': 'success', 'final_response': json.dumps(SCHEMA),
                                       'tool_calls': [{'name': 'web_search'}]})
    assert result['status'] == 'review_needs_work' and not result['analysis_scope_respected']


def test_cloud_handoff_does_not_copy_private_overlay_or_unsolicited_aside():
    value = trial()
    value['trace']['final_response'] = 'Personal aside SECRET_CONTEXT'
    value['trace']['tool_calls'].append({'name': 'web_search', 'params': {'query': 'PRIVATE_QUERY'}})
    report = {'base_sha': 'sha', 'run_id': 'id', 'trials': [value],
              'persona_baseline': {'personal': 'PRIVATE_OVERLAY'}}
    brief = render_handoff(report)
    assert 'forbidden_network_tool_attempt' in brief and 'answer_contract_violation' in brief
    assert 'SECRET_CONTEXT' not in brief and 'PRIVATE_QUERY' not in brief and 'PRIVATE_OVERLAY' not in brief
    assert 'Root causes are unverified' in brief


@pytest.fixture
def live_probe_stub(tmp_path, monkeypatch):
    from evals import live
    from evals import chad_identification_probe as probe
    import evals.preflight.gate as preflight
    (tmp_path / 'evals').mkdir()
    script = tmp_path / 'evals/probe.py'
    script.write_text('synthetic harness')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/config.yaml').write_text('synthetic: true')
    monkeypatch.setattr(probe, '__file__', str(script))
    monkeypatch.setattr(live, 'SERVER_WS', 'ws://localhost:8788/ws')
    monkeypatch.setattr(probe, 'resident_models', lambda: [])
    monkeypatch.setattr(probe, 'snapshot_persona', lambda *args: {'sha256': 'persona'})
    monkeypatch.setattr(probe.subprocess, 'check_output', lambda args, **kwargs: 'sha' if 'rev-parse' in args else '')
    clear = SimpleNamespace(ok=True, manifest=lambda: {'checks': {'server': {'status': 'ok'}}})
    monkeypatch.setattr(preflight, 'run_preflight', lambda *args, **kwargs: clear)
    return probe, live, clear


@pytest.mark.asyncio
async def test_live_probe_is_bounded_and_keeps_passing_and_failing_trials(live_probe_stub, tmp_path, monkeypatch):
    probe, live, _ = live_probe_stub
    from evals.live import LiveTrace
    calls = []
    async def fake_turn(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return LiveTrace(prompt=prompt, category='agent', status='success', final_response='{"count":3,"total":54}')
        folder = prompt.split('local folder: ', 1)[1].split('. Return exactly', 1)[0]
        from pathlib import Path
        answer = json.dumps({'files': sorted(path.name for path in Path(folder).iterdir())})
        if len(calls) == 2:
            answer = f'```json\n{answer}\n```'
        return LiveTrace(prompt=prompt, category='agent', status='success', final_response=answer,
                         tool_calls=[{'name': 'list_directory', 'params': {'path': folder}}])
    monkeypatch.setattr(live, 'probe_live', fake_turn)
    result = await probe.run_identification(tmp_path / 'report.json')
    assert len(calls) == 3 and result['decision'] == 'findings_identified'
    assert [trial['grade']['status'] for trial in result['trials']] == ['failed_contract', 'passed_contract']
    assert result['provenance_unchanged'] and not result['repair_dispatched']
    assert (tmp_path / 'report.json').is_file()


@pytest.mark.asyncio
async def test_failed_preflight_never_launches_behavior(live_probe_stub, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    probe, live, preflight = live_probe_stub
    preflight.ok = False
    turn = AsyncMock()
    monkeypatch.setattr(live, 'probe_live', turn)
    result = await probe.run_identification(tmp_path / 'report.json')
    turn.assert_not_awaited()
    assert result['decision'] == 'inconclusive' and 'Preflight' in result['error']


@pytest.mark.asyncio
async def test_failed_positive_control_never_launches_listing(live_probe_stub, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from evals.live import LiveTrace
    probe, live, _ = live_probe_stub
    turn = AsyncMock(return_value=LiveTrace(prompt='control', category='agent', status='error', error='timeout'))
    monkeypatch.setattr(live, 'probe_live', turn)
    result = await probe.run_identification(tmp_path / 'report.json')
    assert turn.await_count == 1 and not result['trials']
    assert result['decision'] == 'inconclusive' and 'Positive control' in result['error']


@pytest.mark.asyncio
async def test_no_completed_failure_does_not_trigger_self_review(live_probe_stub, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    probe, live, _ = live_probe_stub
    turn = AsyncMock()
    monkeypatch.setattr(live, 'probe_live', turn)
    result = await probe.run_self_review({'trials': [trial()]}, tmp_path / 'review.json')
    turn.assert_not_awaited()
    assert result['status'] == 'no_completed_failure_to_review'
