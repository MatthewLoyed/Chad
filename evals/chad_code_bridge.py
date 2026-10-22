"""Host-side eval bridge for the Chad-on-Chad dev loop (issue #358).

Chad Code runs in a Docker container holding the 35B coder resident. A live eval drives a real
Chad turn, which needs the MAIN model resident. The VRAM budget is 16 GB and both models are
~16-20 GB, so they cannot coexist — the loop is inherently serialized. This service owns that
serialization on the host:

    unload the coder  ->  run the eval (the Brain loads its own model)  ->  reload the coder

The container asks for a verdict over HTTP (``docker/openhands/chad_eval_critic.py``) and is
simply frozen in the meantime. That is safe specifically because the critic runs BETWEEN agent
steps: no inference is in flight, and the container keeps its conversation state in Python
memory, not in VRAM. Evicting the coder mid-step would be a different story.

DEV-ONLY. This is not a user-facing capability and must never ship in a public release: it exists
to let the maintainer's machine grind on eval failures overnight. Chad's *product* self-improvement
(capability suggestions) is a separate system with a far smaller blast radius. See
``evals/SELF_EVAL_DO_NOT_SHIP.md`` for the full list of self-eval-only files to exclude.

Run it:

    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.chad_code_bridge

Safety properties, in order of how much they matter:

1. **Suites are allowlisted.** The container names a key from ``_SUITES``; it never supplies a
   command. Without this, the bridge would be an arbitrary-host-command endpoint for a
   containerized model — the whole point of running the coder in a sandbox.
2. **Every run is capped and reports a structured abort.** A wedged eval comes back as
   ``aborted_reason``, never as silence. The coder must always get a verdict, or it burns its
   whole iteration budget waiting on something that will never finish.
3. **Loopback only by default.** Docker reaches it via ``host.docker.internal``, which resolves
   to the host gateway; binding 0.0.0.0 would expose it to the LAN.
4. **It never touches port 8767.** The production Brain is off limits; the loop targets the dev
   Brain. See docs/guides/PRODUCTION_BRAIN.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8799

# Allowlist: suite key -> argv appended to `python -m evals.runner`. Only free/offline or
# dev-Brain-targeted lanes belong here. Nothing that could hit production :8767, and nothing
# that spends a rate-limited external API (web lanes should be pointed at self-hosted SearXNG
# before they are added here — see issue #358 discussion).
_SUITES: Dict[str, List[str]] = {
    # Free deterministic lanes — no model, no server. Safe to run with the coder still resident,
    # but we swap anyway for uniformity (and because --judge variants would need the model).
    "tool_selection": ["--suite", "tool_selection"],
    "prompt_audit": ["--prompt-audit"],
    "tool_contract": ["--tool-contract"],
    "replay": ["--replay"],
    # Live lanes — need a dev Brain already running. These are the ones worth fixing: MCP tool
    # calls, subagent routing, vague prompts. They are also the ones that need the model swap.
    "smoke": ["--suite", "smoke", "--live"],
    "workflows": ["--suite", "workflows", "--workflow"],
}


# Lanes that drive a REAL Chad turn over a websocket. These are the ones worth fixing (MCP
# calls, subagent routing, vague prompts) and the only ones that can touch a Brain, so they get
# the production-port guard below.
_LIVE_SUITES = frozenset({"smoke", "workflows"})

# evals/live.py defaults to ws://localhost:8767/ws — PRODUCTION. An autonomous loop must never
# drive the production Brain (issue #358 hard requirement; docs/guides/PRODUCTION_BRAIN.md), so
# the bridge always sets CHAD_SERVER_URL explicitly and refuses a value pointing at 8767.
_PROD_BRAIN_PORT = "8767"
_DEFAULT_DEV_SERVER_URL = "ws://localhost:8788/ws"


def _dev_server_url() -> str:
    return (os.environ.get("CHAD_EVAL_SERVER_URL") or _DEFAULT_DEV_SERVER_URL).strip()


def _workspace_roots() -> List[str]:
    """Roots under which a coder workspace may live. The eval runs INSIDE the workspace, so this
    is the boundary that keeps an autonomous run off the real checkout."""
    raw = os.environ.get("CHAD_EVAL_WORKSPACE_ROOTS", "")
    return [p.strip() for p in raw.split(os.pathsep) if p.strip()]


def _validate_workspace(path: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (resolved_cwd, error). The eval must run against the COPY the coder edited, not
    the host checkout — otherwise the fix is never measured and the loop reports 'no improvement'
    forever while the model gets blamed.
    """
    if not path:
        return None, "workspace is required: the eval must run against the code the coder edited"

    roots = _workspace_roots()
    if not roots:
        return None, (
            "CHAD_EVAL_WORKSPACE_ROOTS is not set; refusing to run an eval anywhere. Set it to "
            "the dedicated Chad Code clone root before starting the bridge."
        )

    try:
        resolved = os.path.realpath(path)
    except Exception as exc:  # noqa: BLE001
        return None, f"unusable workspace path: {exc}"

    if not os.path.isdir(resolved):
        return None, f"workspace does not exist: {resolved}"

    inside = False
    for root in roots:
        root_resolved = os.path.realpath(root)
        # commonpath (not startswith) so '/repo-evil' can't pass as being under '/repo'.
        try:
            if os.path.commonpath([resolved, root_resolved]) == root_resolved:
                inside = True
                break
        except ValueError:
            continue  # different drives on Windows

    if not inside:
        return None, (
            f"workspace {resolved} is outside the allowed roots {roots}; refusing to run an eval "
            "against a checkout the loop is not supposed to touch"
        )

    # The workspace must actually BE a Chad checkout, or `python -m evals.runner` dies with
    # "No module named 'evals'" — a Python import error the coder would reasonably try to FIX,
    # wasting an attempt chasing a setup problem it cannot see. Say what's actually wrong.
    if not os.path.isdir(os.path.join(resolved, "evals")):
        return None, (
            f"workspace {resolved} is not a Chad checkout (no evals/ directory), so the eval "
            "runner cannot run there. This is a SETUP problem with the job, not something the "
            "coding task can fix."
        )

    return resolved, None


def _cfg(key: str, default: Any) -> Any:
    try:
        from src.shared.config_manager import ConfigManager

        val = ConfigManager.get(key, default)
        return default if val is None else val
    except Exception:  # noqa: BLE001 — bridge must run even with a broken config
        return default


def _coder_model() -> str:
    return str(_cfg("tools.openhands_coder_model", "openai/qwen/qwen3.6-35b-a3b"))


def _lm_host_url() -> str:
    return str(_cfg("tools.openhands_lmstudio_host_url", "http://localhost:1234/v1"))


async def _unload_coder() -> Optional[str]:
    """Release the coder so the main model has room. Returns an error string, or None on success.

    The coder is loaded with ``owner="warm"`` (see openhands_dispatch._ensure_coder_model), which
    is precisely the owner tag we must drop for the eviction to actually happen — release() only
    unloads once every owner has let go.
    """
    try:
        from src.core.intelligence.model_lifecycle import release

        await release(_lm_host_url(), _coder_model(), owner="warm")
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


async def _reload_coder() -> Optional[str]:
    """Put the coder back so the frozen container's next inference call succeeds."""
    try:
        from src.core.intelligence.model_lifecycle import ensure_model_loaded

        ok = await ensure_model_loaded(_lm_host_url(), _coder_model())
        return None if ok else "ensure_model_loaded returned False"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


async def _workspace_changed(cwd: str, base_ref: str) -> Optional[bool]:
    """Did the coder actually change anything, vs the ref the job started from?

    This lives on the HOST because the SDK hands the critic ``git_patch=None`` unconditionally
    (openhands/sdk/agent/critic_mixin.py, SDK 1.17.0), so the container genuinely cannot tell.
    Counts committed AND uncommitted work — the agent may or may not commit, and either is a
    legitimate way to have done the job.

    Returns None if we can't tell (not a git repo, bad ref); callers treat that as "assume it
    changed" and just run the eval, since a wrong "nothing changed" is far more damaging than a
    wasted eval run.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        if (out or b"").strip():
            return True

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--quiet", base_ref, "HEAD",
            cwd=cwd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode == 0:
            return False   # identical to base
        if proc.returncode == 1:
            return True    # commits differ from base
        return None        # bad ref etc.
    except Exception:  # noqa: BLE001
        return None


async def _run_eval(argv: List[str], timeout_s: float, cwd: Optional[str] = None,
                    extra_env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Run the eval runner as a subprocess. Exit code 0 == pass (the runner's CI contract).

    Killed on timeout, and the kill is reported as ``aborted_reason`` rather than as a failing
    eval — "the harness hung" and "your fix is wrong" must never look the same to the coder.
    """
    cmd = [sys.executable, "-m", "evals.runner", *argv]
    env = {**os.environ, "PYTHONPATH": ".", **(extra_env or {})}
    started = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        # cwd is the whole ballgame: PYTHONPATH="." means the runner imports the code in THIS
        # directory. Run it in the coder's workspace and it measures the coder's fix; run it in
        # the host checkout and it measures code the coder never touched.
        cwd=cwd,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "passed": False,
            "output": "",
            "aborted_reason": f"eval exceeded {timeout_s:.0f}s wall clock and was killed",
            "duration_s": round(time.monotonic() - started, 1),
        }

    text = (out or b"").decode("utf-8", errors="replace")
    # The report tail is where the runner prints its failure summary (which assertion failed).
    # Keep the end; a full run's stdout can be enormous.
    if len(text) > 12000:
        text = "... [report truncated, tail kept] ...\n" + text[-12000:]

    # Attach the FULL per-case trajectories for the failing cases — the model's reasoning,
    # every tool call with params, the plan and round-by-round ledger. The report says WHICH
    # assertion failed; the transcript says WHY the model went wrong, especially in its thinking.
    # That "why" is the whole point of the self-eval loop — a diagnosis needs the reasoning, not
    # just the verdict. Live/workflow lanes write these; free lanes don't (returns "").
    if cwd:
        try:
            from evals.core import read_latest_transcripts

            transcripts = read_latest_transcripts(cwd)
            if transcripts:
                text = f"{text}\n\n{transcripts}"
        except Exception as exc:  # noqa: BLE001 — never let transcript reading fail the verdict
            logger.warning("[ BRIDGE ] transcript read failed (non-fatal): %s", exc)

    return {
        "passed": proc.returncode == 0,
        "output": text,
        "aborted_reason": None,
        "duration_s": round(time.monotonic() - started, 1),
        "exit_code": proc.returncode,
    }


async def _eval_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"passed": False, "aborted_reason": "malformed JSON body"}, status_code=400)

    suite = str(body.get("suite") or "").strip()
    if suite not in _SUITES:
        return JSONResponse(
            {
                "passed": False,
                "aborted_reason": (
                    f"unknown suite {suite!r}; allowed: {sorted(_SUITES)}"
                ),
            },
            status_code=400,
        )

    # The eval must run against the code the coder actually edited, inside an allowed root.
    cwd, ws_err = _validate_workspace(body.get("workspace"))
    if ws_err:
        return JSONResponse({"passed": False, "aborted_reason": ws_err}, status_code=400)

    # Production Brain is off limits, full stop. evals/live.py defaults to :8767, so silence
    # here would mean an autonomous overnight loop hammering production.
    extra_env: Dict[str, str] = {}
    if suite in _LIVE_SUITES:
        server_url = _dev_server_url()
        if _PROD_BRAIN_PORT in server_url:
            return JSONResponse(
                {
                    "passed": False,
                    "aborted_reason": (
                        f"refusing to run live suite {suite!r} against {server_url}: port "
                        f"{_PROD_BRAIN_PORT} is the PRODUCTION Brain. Point "
                        "CHAD_EVAL_SERVER_URL at the dev Brain (:8788)."
                    ),
                },
                status_code=400,
            )
        extra_env["CHAD_SERVER_URL"] = server_url

    try:
        timeout_s = float(body.get("timeout_s") or 1800)
    except (TypeError, ValueError):
        timeout_s = 1800.0
    # The caller's timeout covers the swaps too; leave headroom so the eval is killed by US
    # (structured abort) rather than by the container giving up on the socket.
    eval_timeout = max(60.0, timeout_s - 300.0)

    argv = list(_SUITES[suite])
    # NOTE: evals/runner.py has NO --case flag (only --limit). Appending one made the runner
    # exit on an argparse error, which the coder would have seen as a failing eval rather than
    # a broken invocation. Carried through for reporting only until the runner grows a real
    # case filter; do not turn it back into an argv flag without checking runner.py first.
    case_id = body.get("case_id")

    logger.warning("[ BRIDGE ] eval request suite=%s case=%s timeout=%.0f", suite, case_id, timeout_s)

    # No-op guard: if the coder changed nothing, skip a full unload/eval/reload cycle (minutes)
    # on a guaranteed non-result, and tell it plainly. Only skips on a confident False — an
    # unknown answer runs the eval, because a wrong "nothing changed" is far worse than a
    # wasted run.
    base_ref = str(body.get("base_ref") or "HEAD")
    if await _workspace_changed(cwd, base_ref) is False:
        return JSONResponse({
            "passed": False,
            "output": "",
            "aborted_reason": None,
            "duration_s": 0.0,
            "suite": suite,
            "case_id": case_id,
            "no_changes": True,
            "message": (
                "No changes were made to the workspace, so the eval was not run — there is "
                "nothing to evaluate. Edit the source files needed to fix the failure."
            ),
        })

    unload_err = await _unload_coder()
    if unload_err:
        # Not fatal on its own — LM Studio may still fit both, or the model may not be resident.
        # Log it and continue; the eval will tell us if VRAM was actually the problem.
        logger.warning("[ BRIDGE ] coder unload failed (continuing): %s", unload_err)

    try:
        result = await _run_eval(argv, eval_timeout, cwd=cwd, extra_env=extra_env)
    finally:
        # ALWAYS put the coder back, even if the eval blew up — the container is frozen waiting
        # on this response and its next inference call needs the model resident.
        reload_err = await _reload_coder()
        if reload_err:
            logger.error("[ BRIDGE ] coder reload FAILED: %s", reload_err)

    result["suite"] = suite
    result["case_id"] = case_id
    logger.warning(
        "[ BRIDGE ] eval done suite=%s passed=%s duration=%ss aborted=%s",
        suite, result.get("passed"), result.get("duration_s"), result.get("aborted_reason"),
    )
    return JSONResponse(result)


async def _health_endpoint(request: Request) -> JSONResponse:  # noqa: ARG001
    return JSONResponse({"ok": True, "suites": sorted(_SUITES), "coder_model": _coder_model()})


app = Starlette(routes=[
    Route("/eval", _eval_endpoint, methods=["POST"]),
    Route("/health", _health_endpoint, methods=["GET"]),
])


def main() -> int:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("CHAD_BRIDGE_PORT", DEFAULT_PORT))
    # Loopback only: Docker reaches this through host.docker.internal (the host gateway), so
    # binding 0.0.0.0 would expose an eval-triggering endpoint to the LAN for no benefit.
    host = os.environ.get("CHAD_BRIDGE_HOST", "127.0.0.1")
    print(f"[ BRIDGE ] chad_code eval bridge on http://{host}:{port} suites={sorted(_SUITES)}")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
