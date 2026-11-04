"""Real-process completion gates: failures cannot become successful delivery."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

PATH = Path(__file__).resolve().parents[2] / 'docker/openhands/chad_completion.py'
spec = importlib.util.spec_from_file_location('chad_completion', PATH)
gate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate_module)


def gate(tmp_path, commands):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    contract = tmp_path / 'contract.json'
    contract.write_text(json.dumps(gate_module.contract_for(workspace, commands)))
    return gate_module.CompletionGate(workspace, contract, tmp_path / 'artifacts')


def test_failure_then_model_repair_requires_new_success(tmp_path):
    checker = gate(tmp_path, [[sys.executable, '-c',
        'from pathlib import Path; raise SystemExit(0 if Path("fixed").exists() else 1)']])
    first = checker.verify()
    assert first['status'] == 'failed'
    assert first['checks'][0]['exit_code'] == 1
    (checker.workspace / 'fixed').touch()
    second = checker.verify()
    assert second['status'] == 'passed'
    assert first['fingerprint'] != second['fingerprint']
    assert len((checker.artifacts / 'completion-history.jsonl').read_text().splitlines()) == 2


def test_source_changed_during_verification_is_stale(tmp_path):
    checker = gate(tmp_path, [[sys.executable, '-c', 'from pathlib import Path; Path("changed").touch()']])
    assert checker.verify()['status'] == 'stale'


@pytest.mark.parametrize('commands', [[], [['missing-fictional-check-executable']]])
def test_unconfigured_and_missing_checks_never_pass(tmp_path, commands):
    assert gate(tmp_path, commands).verify()['status'] == 'infra_error'


def test_contract_pins_check_scripts(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    package = workspace / 'package.json'; package.write_text('{"scripts":{"test":"vitest run"}}')
    contract = tmp_path / 'contract.json'
    contract.write_text(json.dumps(gate_module.contract_for(workspace)))
    package.write_text('{"scripts":{"test":"echo passing"}}')
    checker = gate_module.CompletionGate(workspace, contract, tmp_path / 'artifacts')
    assert checker.verify()['status'] == 'infra_error'


def test_hook_denies_failed_checks(tmp_path):
    checker = gate(tmp_path, [[sys.executable, '-c', 'raise SystemExit(7)']])
    result = subprocess.run([sys.executable, str(PATH), str(checker.workspace),
                             str(checker.contract), str(checker.artifacts)], capture_output=True, text=True)
    assert json.loads(result.stdout)['decision'] == 'deny'
    assert json.loads(checker.receipt.read_text())['checks'][0]['exit_code'] == 7


def test_all_checks_recorded_without_shell_masking(tmp_path):
    checker = gate(tmp_path, [[sys.executable, '-c', 'raise SystemExit(3)'],
                             [sys.executable, '-c', 'print("second check")']])
    state = checker.verify()
    assert state['status'] == 'failed'
    assert [x['exit_code'] for x in state['checks']] == [3, 0]
