"""Regression tests for #632 — dry-run residency + incremental persist + concurrency default."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from evals.core import Case, CaseResult
from evals.runner import (
    DEFAULT_DRY_CONCURRENCY,
    _case_payload,
    _persist,
    _write_run_json,
)


def test_default_dry_concurrency_is_parallel():
    assert DEFAULT_DRY_CONCURRENCY >= 4


def test_incremental_persist_keeps_partial_snapshot(tmp_path, monkeypatch):
    from evals import runner as runner_mod
    from evals import core as core_mod

    monkeypatch.setattr(runner_mod, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(core_mod, "RUNS_DIR", tmp_path)

    r1 = CaseResult("c1", "p1", passed=True, lane="dry-run")
    r1.classify()
    r2 = CaseResult("c2", "p2", passed=False, error="boom", lane="dry-run")
    r2.classify()

    ts = "20260724_120000"
    path = _write_run_json(
        "tool_selection",
        [r1, None, r2],
        do_judge=False,
        lane="dry-run",
        manifest={},
        ts=ts,
        partial=True,
        planned=3,
    )
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["partial"] is True
    assert payload["completed"] == 2
    assert payload["planned"] == 3
    assert [c["id"] for c in payload["cases"]] == ["c1", "c2"]
    assert (tmp_path / "latest.md").exists()
    assert "partial run" in (tmp_path / "latest.md").read_text(encoding="utf-8").lower()


def test_finalize_persist_clears_partial_flag(tmp_path, monkeypatch):
    from evals import runner as runner_mod
    from evals import core as core_mod
    import evals.scoreboard as scoreboard

    monkeypatch.setattr(runner_mod, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(core_mod, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(scoreboard, "append_history", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_mod, "persist_transcripts", lambda *a, **k: tmp_path / "t")
    monkeypatch.setattr(runner_mod, "cluster_failures", lambda *_a, **_k: [])
    monkeypatch.setattr(runner_mod, "render_triage", lambda *a, **k: "# triage\n")

    case = Case(id="c1", prompt="p")
    result = CaseResult("c1", "p", passed=True, lane="dry-run")
    result.classify()

    ok = _persist(
        "tool_selection", [case], [result], do_judge=False, lane="dry-run",
        manifest={}, ts="20260724_130000",
    )
    payload = json.loads((tmp_path / "run_20260724_130000.json").read_text(encoding="utf-8"))
    assert payload["partial"] is False
    assert payload["completed"] == 1
    assert isinstance(ok, bool)


def test_dry_run_disables_selector_unload(monkeypatch):
    """Dry-run must keep the 9B resident — unload-after-select is the #632 thrash."""
    from evals.dry_run import DryRunHarness

    captured = {}

    class _Cfg:
        @staticmethod
        def load_config():
            return {}

        @staticmethod
        def get_config():
            return captured.setdefault("cfg", {})

    monkeypatch.setattr("src.shared.config_manager.ConfigManager.load_config", _Cfg.load_config)
    monkeypatch.setattr("src.shared.config_manager.ConfigManager.get_config", _Cfg.get_config)

    class _Boom(Exception):
        pass

    def _boom_event_bus(*_a, **_k):
        raise _Boom("stop-after-config")

    monkeypatch.setattr("src.application.events.EventBus", _boom_event_bus)

    harness = DryRunHarness(quiet=True)
    try:
        with pytest.raises(_Boom):
            asyncio.run(harness.setup())
    finally:
        # harness.setup() sets CHAD_TESTING via os.environ.setdefault (fine in a
        # real, throwaway dry-run process, but this is a unit test in the shared
        # pytest session — undo it so it doesn't leak into later tests, same
        # class of bug as the CHAD_TESTING leaks fixed for #643).
        import os
        os.environ.pop("CHAD_TESTING", None)

    assert captured["cfg"]["tools"]["llm_selector_unload_after_select"] is False
    assert captured["cfg"]["tools"]["llm_selector_fail_fast"] is False
    assert captured["cfg"]["local_models"]["lifecycle"]["preload_on_connect"] is False


def test_probe_live_reuses_provided_ws(monkeypatch):
    """Suite-level open_eval_ws must skip a second connect+auth per case."""
    from contextlib import asynccontextmanager
    import evals.live as live

    sent = []

    class _FakeWS:
        async def send(self, raw):
            sent.append(json.loads(raw))

    async def _fake_collect(ws, trace, reasoning, results, *, expected_conv=None):
        trace.final_response = "ok"
        trace.status = "success"

    monkeypatch.setattr(live, "_collect", _fake_collect)
    opened = {"n": 0}

    @asynccontextmanager
    async def _boom_open(*_a, **_k):
        opened["n"] += 1
        yield _FakeWS()

    monkeypatch.setattr(live, "open_eval_ws", _boom_open)
    monkeypatch.setattr(live, "load_auth_token", lambda: "tok")

    async def _run():
        shared = _FakeWS()
        t1 = await live.probe_live("hi", ws=shared, auth_token="tok")
        t2 = await live.probe_live("hi again", ws=shared, auth_token="tok")
        return t1, t2

    t1, t2 = asyncio.run(_run())
    assert opened["n"] == 0  # must not open a new socket when ws is provided
    assert t1.error is None and t2.error is None
    assert t1.final_response == "ok"
    assert len(sent) == 2
    assert all(m["type"] == "user_input" for m in sent)


def test_selector_skips_unload_while_eval_active(monkeypatch):
    """Live eval must not unload the 9B after select — that thrash was ~20–30s/case."""
    from src.core.orchestration import eval_exclusivity as ee
    from src.infrastructure.tools.selection.llm_selector import LLMToolSelector

    ee.reset_for_tests()
    released = []

    sel = LLMToolSelector.__new__(LLMToolSelector)
    sel._unload_after_select = True
    sel._resolved_model_id = "qwen/qwen3.5-9b"
    sel._model_id = "qwen/qwen3.5-9b"
    sel._main_model_hint = "google/gemma-4-26b-a4b-qat"
    sel._base_url = "http://test.local/v1"
    sel._OWNER = "tool_selector"
    sel._match_model_id = lambda want, have: any(want.split("/")[-1] in (h or "") for h in have)
    sel._loaded_models_cache = ["x"]
    sel._loaded_models_cache_time = 1.0

    monkeypatch.setattr(
        "src.infrastructure.tools.selection.llm_selector.release_sync",
        lambda *a, **k: released.append((a, k)),
    )

    async def _with_eval():
        async with ee.eval_turn_guard("eval"):
            sel._maybe_unload_after_use()
            await asyncio.sleep(0.05)  # unload path is fire-and-forget thread when it runs

    asyncio.run(_with_eval())
    assert released == [], "eval-active unload must be a no-op"
    assert sel._resolved_model_id == "qwen/qwen3.5-9b"


def test_case_payload_shape():
    r = CaseResult("x", "y", passed=True, lane="dry-run")
    r.classify()
    p = _case_payload(r)
    assert p["id"] == "x"
    assert p["passed"] is True
    assert "metrics" in p


def test_progress_printer_shows_tally_and_eta(capsys):
    from evals.runner import _ProgressPrinter
    import time

    prog = _ProgressPrinter(4)
    prog.t0 = time.perf_counter() - 10.0  # pretend 10s already elapsed
    prog.record("a", True, detail="(ok)")
    prog.record("b", False)
    out = capsys.readouterr().out
    assert "[1/4] a: PASS" in out
    assert "[2/4] b: FAIL" in out
    assert "1 pass · 1 fail" in out
    assert "2 left" in out
    assert "elapsed" in out


def test_selector_skips_unload_when_model_is_pinned(monkeypatch):
    """A pinned selector must stay resident: unloading it would pay the reload next turn
    while keeping none of the pin's benefit (under the pin, that reload no longer evicts
    the main model, so the swap it used to 'save' doesn't exist anymore)."""
    from src.infrastructure.tools.selection.llm_selector import LLMToolSelector

    released = []

    sel = LLMToolSelector.__new__(LLMToolSelector)
    sel._unload_after_select = True
    sel._resolved_model_id = "qwen/qwen3.5-9b"
    sel._model_id = "qwen/qwen3.5-9b"
    sel._main_model_hint = "google/gemma-4-26b-a4b-qat"
    sel._base_url = "http://test.local/v1"
    sel._OWNER = "tool_selector"
    sel._match_model_id = lambda want, have: any(want.split("/")[-1] in (h or "") for h in have)
    sel._loaded_models_cache = ["x"]
    sel._loaded_models_cache_time = 1.0

    monkeypatch.setattr(
        "src.infrastructure.tools.selection.llm_selector.release_sync",
        lambda *a, **k: released.append((a, k)),
    )
    monkeypatch.setattr("src.core.intelligence.model_lifecycle.is_pinned", lambda m: True)

    sel._maybe_unload_after_use()
    time.sleep(0.05)  # unload path is a fire-and-forget thread when it does run

    assert released == [], "pinned selector must not be unloaded"
    assert sel._resolved_model_id == "qwen/qwen3.5-9b"
