"""Exercise real child processes and filesystem edits, without model inference."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2] / "docker/openhands"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tools = load("chad_coder_tools")
trace_module = load("chad_run_trace")


def test_command_retains_full_log_and_failure(tmp_path, capsys):
    code = tools.run_command([sys.executable, "-c", "print('evidence\\n' * 20); raise SystemExit(7)"], tmp_path)
    assert code == 7
    receipt = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert receipt["exit_code"] == 7
    assert receipt["state"] == "finished"
    assert receipt["duration_s"] >= 0
    assert Path(receipt["log"]).read_text().count("evidence") == 20
    assert capsys.readouterr().out.count("evidence") < 20


def test_missing_executable_is_reported(tmp_path):
    assert tools.run_command([str(tmp_path / "absent-executable")], tmp_path) == 127
    receipt = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert receipt["exit_code"] == 127
    assert "FileNotFoundError" in Path(receipt["log"]).read_text()


@pytest.mark.parametrize("before,old", [("abc abc", "abc"), ("abc", "missing"), ("abc", "")])
def test_rejected_replacements_do_not_change_file(tmp_path, before, old):
    target = tmp_path / "source.txt"
    target.write_text(before)
    with pytest.raises(ValueError):
        tools.replace_exact(target, old, "new")
    assert target.read_text() == before


def test_exact_edit_preserves_crlf_and_reports_diff(tmp_path, capsys):
    target = tmp_path / "source.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    tools.replace_exact(target, "two", "three")
    assert target.read_bytes() == b"one\r\nthree\r\n"
    assert "+three" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == [target]


def test_cli_argv_preserves_spaces_and_shell_metacharacters(tmp_path):
    import os

    result = subprocess.run(
        [sys.executable, str(ROOT / "chad_coder_tools.py"), "run", "--", sys.executable,
         "-c", "import sys; print(sys.argv[1])", "spaces & $literal"],
        env={**os.environ, "CHAD_RUN_ARTIFACTS": str(tmp_path)}, capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "spaces & $literal" in result.stdout


def test_failed_atomic_replace_preserves_original(tmp_path, monkeypatch):
    target = tmp_path / "source.txt"
    target.write_text("old")

    def denied(*args):
        raise PermissionError("fixture refusal")

    monkeypatch.setattr(tools.os, "replace", denied)
    with pytest.raises(PermissionError):
        tools.replace_exact(target, "old", "new")
    assert target.read_text() == "old"
    assert list(tmp_path.iterdir()) == [target]


def test_trace_captures_public_reply_without_reasoning(tmp_path):
    trace = trace_module.RunTrace(tmp_path)
    trace(SimpleNamespace(llm_message=SimpleNamespace(
        role="assistant", content=[SimpleNamespace(text="I have finished.")],
        reasoning_content="private reasoning"), reasoning_content="private reasoning"))
    record = json.loads(trace.events.read_text())
    assert record["message"] == {"role": "assistant", "text": ["I have finished."]}
    assert "private reasoning" not in trace.events.read_text()


def test_trace_keeps_actions_ids_and_complete_observations(tmp_path):
    class Payload:
        def model_dump(self, mode):
            return {"output": "full output" * 100, "exit_code": 3}

    trace = trace_module.RunTrace(tmp_path)
    trace.record("run_start")
    trace(SimpleNamespace(id="event1", tool_call_id="call1", tool_name="terminal",
                          observation=Payload(), llm_message="not explicitly recorded"))
    records = [json.loads(line) for line in trace.events.read_text().splitlines()]
    assert records[1]["tool_call_id"] == "call1"
    assert records[1]["observation"]["exit_code"] == 3
    assert records[1]["observation"]["output"] == "full output" * 100
    assert "llm_message" not in records[1]
    assert records[1]["elapsed_s"] >= records[0]["elapsed_s"]
