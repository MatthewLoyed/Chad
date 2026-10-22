"""Unit tests for evals/chad_on_chad_driver.py -- the parts that don't need Docker/LM Studio.

The full run_cycle() needs a real Chad Code container + local model and is exercised manually
(see docs/architecture/CHAD_SELF_EVAL_LOOP.md), not here. These tests cover the pure/composable
pieces: candidate loading and the subprocess-based AFTER runner's error paths."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evals import chad_on_chad_driver as driver
import src.tools.dev.openhands_dispatch as openhands_dispatch


def test_load_candidate_finds_known_case():
    case = driver.load_candidate("annual-report-advisor-initials")
    assert case["prompt"] == "find my smart annual report and check for advisor initials"
    assert case["status"] == "run"


def test_load_candidate_unknown_id_raises():
    with pytest.raises(KeyError):
        driver.load_candidate("does-not-exist")


def test_diagnosis_prompt_includes_original_prompt_and_trace():
    case = {"prompt": "some eval prompt", "why": "x"}
    prompt = driver._diagnosis_prompt(case, {"response": "hi", "tool_calls": []})
    assert "some eval prompt" in prompt
    assert "eval_diagnosis skill" in prompt
    assert '"response": "hi"' in prompt


def test_judge_prompt_includes_both_traces():
    case = {"prompt": "p", "why": "w"}
    prompt = driver._judge_prompt(case, {"tag": "before"}, {"tag": "after"})
    assert '"tag": "before"' in prompt
    assert '"tag": "after"' in prompt
    assert "IMPROVED" in prompt


def test_run_after_reports_missing_trace_marker(tmp_path, monkeypatch):
    """If the subprocess doesn't print the JSON marker (e.g. it crashed before finishing),
    run_after must report a clear error instead of raising."""
    import subprocess as subprocess_mod

    class FakeCompleted:
        returncode = 1
        stdout = "some traceback output, no marker here"
        stderr = "boom"

    monkeypatch.setattr(subprocess_mod, "run", lambda *a, **k: FakeCompleted())
    result = driver.run_after("prompt", clone_path=tmp_path, secrets_env={})
    assert "error" in result
    assert "no trace JSON" in result["error"]


def test_run_after_parses_trace_marker(tmp_path, monkeypatch):
    import subprocess as subprocess_mod

    trace = {"response": "ok", "tool_calls": []}
    stdout = "some log noise\n===CHAD_TRACE_JSON===\n" + json.dumps(trace)

    class FakeCompleted:
        returncode = 0
        stderr = ""

    fc = FakeCompleted()
    fc.stdout = stdout
    monkeypatch.setattr(subprocess_mod, "run", lambda *a, **k: fc)
    result = driver.run_after("prompt", clone_path=tmp_path, secrets_env={})
    assert result == trace


async def test_dispatch_fix_runs_from_inside_an_event_loop(monkeypatch, tmp_path):
    """Regression test for a real bug caught live 2026-07-23: dispatch_fix used to wrap its call
    in asyncio.run(), which raises 'asyncio.run() cannot be called from a running event loop'
    when invoked (as run_cycle always does) from inside an already-running loop. This test IS
    that context -- pytest-asyncio runs it inside a loop -- so a regression would fail here."""
    captured = {}

    class FakeDispatchTool:
        async def execute(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "result": {"job_id": "fake123"}}

    monkeypatch.setattr(openhands_dispatch, "OpenHandsDispatchTool", FakeDispatchTool)

    clone = {"path": str(tmp_path), "allowlist_env": {"CHAD_CODE_EXTRA_ALLOWLIST": str(tmp_path)}}
    result = await driver.dispatch_fix("do the fix", clone, title="test job")

    assert result["success"] is True
    assert result["result"]["job_id"] == "fake123"
    assert captured["workspace"] == str(tmp_path)
    assert "do the fix" in captured["task"]
    # allowlist env grant must be scoped to the call, not leaked afterward
    assert "CHAD_CODE_EXTRA_ALLOWLIST" not in os.environ
