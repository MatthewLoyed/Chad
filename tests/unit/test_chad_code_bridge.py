"""Safety contract for the Chad-on-Chad eval bridge (issue #358).

The bridge lets a containerized local model trigger commands on the host, so its guarantees are
the thing worth testing — not its happy path:

  * a suite name can never become a command (allowlist),
  * a wedged eval always produces a structured verdict rather than silence,
  * the coder model is put back even when the eval blows up (the container is frozen waiting).
"""

import asyncio

import pytest

from evals import chad_code_bridge as bridge


class _Body:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _post(payload):
    return asyncio.run(bridge._eval_endpoint(_Body(payload)))


def _mk_ws(path):
    """A workspace the bridge will accept: exists and looks like a Chad checkout."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "evals").mkdir(exist_ok=True)
    return str(path)


def test_unknown_suite_is_rejected_and_never_executed():
    """A suite name is an allowlist key, never a command. Without this the bridge is an
    arbitrary-host-command endpoint for a sandboxed model."""
    resp = _post({"suite": "; rm -rf /"})
    assert resp.status_code == 400
    assert b"unknown suite" in resp.body


def test_malformed_body_is_rejected():
    class _Bad:
        async def json(self):
            raise ValueError("not json")

    resp = asyncio.run(bridge._eval_endpoint(_Bad()))
    assert resp.status_code == 400


def test_allowlist_has_no_production_brain_lane():
    """Prod Brain (:8767) is off limits — the loop targets the dev Brain."""
    for argv in bridge._SUITES.values():
        assert "8767" not in " ".join(argv)


def test_live_suite_refuses_production_brain(monkeypatch, tmp_path):
    """evals/live.py defaults to ws://localhost:8767/ws — PRODUCTION. An overnight autonomous
    loop pointed at production is the single worst outcome here, so the bridge refuses."""
    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(tmp_path))
    monkeypatch.setenv("CHAD_EVAL_SERVER_URL", "ws://localhost:8767/ws")

    resp = _post({"suite": "smoke", "workspace": _mk_ws(tmp_path)})
    assert resp.status_code == 400
    assert b"PRODUCTION" in resp.body


def test_live_suite_pins_dev_server_url(monkeypatch, tmp_path):
    """The dev URL must be passed explicitly — relying on live.py's default is what would send
    the run to production."""
    seen = {}

    async def _capture(argv, timeout_s, cwd=None, extra_env=None):
        seen["env"] = extra_env or {}
        return {"passed": True, "output": "", "aborted_reason": None, "duration_s": 0.0}

    async def _noop():
        return None

    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(tmp_path))
    monkeypatch.delenv("CHAD_EVAL_SERVER_URL", raising=False)
    monkeypatch.setattr(bridge, "_run_eval", _capture)
    monkeypatch.setattr(bridge, "_unload_coder", _noop)
    monkeypatch.setattr(bridge, "_reload_coder", _noop)

    _post({"suite": "smoke", "workspace": _mk_ws(tmp_path)})
    assert seen["env"]["CHAD_SERVER_URL"] == bridge._DEFAULT_DEV_SERVER_URL
    assert "8767" not in seen["env"]["CHAD_SERVER_URL"]


def test_eval_runs_in_the_coder_workspace_not_the_host_checkout(monkeypatch, tmp_path):
    """PYTHONPATH='.' means cwd decides which code is under test. Wrong cwd = the fix is never
    measured and the loop blames the model for 'no improvement' forever."""
    seen = {}

    async def _capture(argv, timeout_s, cwd=None, extra_env=None):
        seen["cwd"] = cwd
        return {"passed": True, "output": "", "aborted_reason": None, "duration_s": 0.0}

    async def _noop():
        return None

    ws = tmp_path / "job-1"
    ws.mkdir()
    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(tmp_path))
    monkeypatch.setattr(bridge, "_run_eval", _capture)
    monkeypatch.setattr(bridge, "_unload_coder", _noop)
    monkeypatch.setattr(bridge, "_reload_coder", _noop)

    _post({"suite": "tool_contract", "workspace": _mk_ws(ws)})
    assert seen["cwd"] == str(ws.resolve())


def test_workspace_outside_allowed_roots_is_refused(monkeypatch, tmp_path):
    """The boundary that keeps an autonomous run off the real checkout."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "the_real_repo"
    outside.mkdir()
    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(allowed))

    resp = _post({"suite": "tool_contract", "workspace": str(outside)})
    assert resp.status_code == 400
    assert b"outside the allowed roots" in resp.body


def test_prefix_sibling_cannot_masquerade_as_allowed_root(monkeypatch, tmp_path):
    """'/x/repo-evil' must not pass as being under '/x/repo' — hence commonpath, not startswith."""
    root = tmp_path / "repo"
    root.mkdir()
    evil = tmp_path / "repo-evil"
    evil.mkdir()
    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(root))

    resp = _post({"suite": "tool_contract", "workspace": str(evil)})
    assert resp.status_code == 400


def test_missing_workspace_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(tmp_path))
    resp = _post({"suite": "tool_contract"})
    assert resp.status_code == 400
    assert b"workspace is required" in resp.body


def test_unset_roots_refuses_everything(monkeypatch, tmp_path):
    """Fail closed: with no configured roots the bridge runs nothing, rather than defaulting to
    somewhere convenient like its own cwd (which is the real checkout)."""
    monkeypatch.delenv("CHAD_EVAL_WORKSPACE_ROOTS", raising=False)
    resp = _post({"suite": "tool_contract", "workspace": _mk_ws(tmp_path)})
    assert resp.status_code == 400
    assert b"CHAD_EVAL_WORKSPACE_ROOTS" in resp.body


def test_non_checkout_workspace_gives_an_actionable_message(monkeypatch, tmp_path):
    """Without this the coder sees "No module named 'evals'" — a Python import error it would
    reasonably try to FIX, burning an attempt on a setup problem it cannot see."""
    ws = tmp_path / "empty"
    ws.mkdir()
    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(tmp_path))

    resp = _post({"suite": "tool_contract", "workspace": str(ws)})
    assert resp.status_code == 400
    assert b"not a Chad checkout" in resp.body
    assert b"SETUP problem" in resp.body


def test_hung_eval_reports_structured_abort_not_silence(monkeypatch):
    """A timeout must be distinguishable from a failing eval. If they look the same, the coder
    'fixes' code that was never actually broken, and burns its whole iteration budget."""

    async def _never_finishes(*_a, **_k):
        class _Proc:
            returncode = None

            async def communicate(self):
                await asyncio.sleep(60)
                return b"", b""

            def kill(self):
                self.returncode = -9

            async def wait(self):
                return -9

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _never_finishes)
    result = asyncio.run(bridge._run_eval(["--tool-contract"], timeout_s=0.2))

    assert result["passed"] is False
    assert result["aborted_reason"], "a killed eval must say so"
    assert "killed" in result["aborted_reason"]


def test_coder_is_reloaded_even_when_eval_raises(monkeypatch, tmp_path):
    """The container is frozen waiting on this response; its next inference call needs the
    coder resident. A crash in the eval must not strand it."""
    reloaded = []

    async def _boom(*_a, **_k):
        raise RuntimeError("eval exploded")

    async def _noop_unload():
        return None

    async def _track_reload():
        reloaded.append(True)
        return None

    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(tmp_path))
    monkeypatch.setattr(bridge, "_run_eval", _boom)
    monkeypatch.setattr(bridge, "_unload_coder", _noop_unload)
    monkeypatch.setattr(bridge, "_reload_coder", _track_reload)

    with pytest.raises(RuntimeError):
        _post({"suite": "tool_contract", "workspace": _mk_ws(tmp_path)})

    assert reloaded == [True], "coder must be reloaded in the finally block"


def test_eval_timeout_leaves_headroom_for_model_swaps(monkeypatch, tmp_path):
    """The caller's budget covers two ~60-120s model swaps as well as the suite, so the eval
    itself is capped lower — we want OUR structured abort, not the container's socket giving up."""
    seen = {}

    async def _capture(argv, timeout_s, cwd=None, extra_env=None):
        seen["timeout_s"] = timeout_s
        return {"passed": True, "output": "", "aborted_reason": None, "duration_s": 0.0}

    async def _noop():
        return None

    monkeypatch.setenv("CHAD_EVAL_WORKSPACE_ROOTS", str(tmp_path))
    monkeypatch.setattr(bridge, "_run_eval", _capture)
    monkeypatch.setattr(bridge, "_unload_coder", _noop)
    monkeypatch.setattr(bridge, "_reload_coder", _noop)

    _post({"suite": "tool_contract", "timeout_s": 1800, "workspace": _mk_ws(tmp_path)})
    assert seen["timeout_s"] == 1500.0

    # Never goes non-positive on a small budget.
    _post({"suite": "tool_contract", "timeout_s": 10, "workspace": _mk_ws(tmp_path)})
    assert seen["timeout_s"] >= 60.0
