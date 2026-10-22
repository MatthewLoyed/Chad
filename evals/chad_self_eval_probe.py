"""Escalating probe for the Chad-on-Chad self-eval loop (issue #358).

DEV-ONLY — must never ship in a public release. See evals/SELF_EVAL_DO_NOT_SHIP.md.

Drives the REAL tools (dispatch_coding_task / check_coding_task) with the eval critic attached,
so a run exercises the whole chain: dispatch -> container -> agent edits -> critic -> host bridge
-> model swap -> eval -> verdict -> retry-or-finish.

Rungs, deliberately escalating. Each isolates ONE new variable, because five novel pieces are
stacked here (critic hook, bridge, model swap, workspace boundary, model fix quality) and a
failure on a big task tells you nothing about which one broke.

  0  plumbing   trivial task + an already-PASSING suite. Proves the chain end-to-end without
                asking the model to be smart. If this fails, nothing else is worth running.
  1  seeded     a deliberately broken file with a KNOWN correct fix. Proves the model can
                diagnose+fix under the critic, and can be graded objectively rather than by
                arguing about whether its patch was tasteful.
  2  real       a genuine offline-lane failure.
  3  live       a real live-lane failure (subagent routing / vague prompt) against the dev Brain.

Prerequisites: Docker up, LM Studio serving the coder, the bridge running with
CHAD_EVAL_WORKSPACE_ROOTS set, and (rung 3 only) a dev Brain on :8788.

    $env:PYTHONPATH="."; .\\.venv\\Scripts\\python.exe -m evals.chad_self_eval_probe --rung 0
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# The coder's OWN clone of the Chad repo to work in (not the host checkout). This is a personal,
# machine-specific path, so it is NOT hardcoded here — tracked files carry no personal paths (see
# CLAUDE.md, "Private data"). Set CHAD_CODE_WORKSPACE, or pass --workspace.
_DEFAULT_WS = os.environ.get("CHAD_CODE_WORKSPACE", "")
_BRIDGE = "http://host.docker.internal:8799"

# Live lanes drive a REAL Chad turn over a websocket, so capturing their failure on the host
# must target the DEV Brain (:8788), never production (:8767). evals/live.py defaults to :8767
# when CHAD_SERVER_URL is unset, so the probe sets it explicitly and refuses a prod value —
# mirroring the same guard the bridge applies for the verification re-run.
_LIVE_SUITES = frozenset({"smoke", "workflows"})
_PROD_BRAIN_PORT = "8767"
_DEFAULT_DEV_SERVER_URL = "ws://localhost:8788/ws"


def _dev_server_url() -> str:
    url = (os.environ.get("CHAD_EVAL_SERVER_URL") or _DEFAULT_DEV_SERVER_URL).strip()
    if _PROD_BRAIN_PORT in url:
        raise SystemExit(
            f"refusing to capture a live-suite failure against {url}: port {_PROD_BRAIN_PORT} "
            "is the PRODUCTION Brain. Point CHAD_EVAL_SERVER_URL at the dev Brain (:8788)."
        )
    return url


def _seed_tool_contract_failure(ws: Path) -> tuple[str, str]:
    """Rung 1: remove the search_images TOOL_METADATA entry so --tool-contract fails.

    Chosen because the correct fix is exactly one line, the eval runs in ~2s (fast iterations),
    and we know the answer — so 'did the model fix it' is a fact, not a judgement call.
    """
    target = ws / "src" / "clients" / "web" / "src" / "utils" / "tool-ui.tsx"
    original = target.read_text(encoding="utf-8")
    marker = "    search_images: {"
    line = next((ln for ln in original.splitlines() if ln.startswith(marker)), None)
    if line is None:
        raise SystemExit(f"seed failed: no search_images entry found in {target}")
    seeded = original.replace(line + "\n", "")
    target.write_text(seeded, encoding="utf-8")
    return str(target), line


RUNGS = {
    0: {
        "title": "chad-self-eval rung 0 (plumbing)",
        "suite": "tool_contract",
        # The critic only runs when the agent FINISHES (mode='finish_and_message'), and each
        # refinement retry spends from the same budget. Hitting the cap means the run ends with
        # no verification at all — which is exactly what happened at 8 (the agent spent several
        # iterations fighting a missing git identity). Leave real headroom.
        "max_iterations": 30,
        "task": (
            "Create a file named PLUMBING_OK.txt in the repository root containing the single "
            "line: chad self-eval plumbing check.\n\n"
            "Do not modify any other file. This is a connectivity check, nothing more.\n\n"
            "You do NOT need to commit; leaving the change in the working tree is fine.\n\n"
            "IMPORTANT: after you finish, an automated verification step runs on its own. You "
            "cannot run it yourself and do not need to — just make the change above and finish."
        ),
    },
    1: {
        "title": "chad-self-eval rung 1 (seeded tool-contract failure)",
        "suite": "tool_contract",
        "max_iterations": 25,
        "seed": _seed_tool_contract_failure,
        "task": (
            "The repository's native-tool contract check is FAILING. The failure output is "
            "reproduced below — you do NOT need to run it, and you CANNOT: this sandbox has "
            "none of the repository's Python dependencies installed, so "
            "`python -m evals.runner` will only ever raise ModuleNotFoundError. Do not spend "
            "turns trying. Verification runs automatically after you finish.\n\n"
            "{failure}\n\n"
            "Every native tool must have a `TOOL_METADATA` entry in "
            "`src/clients/web/src/utils/tool-ui.tsx`; the check fails on any tool that does not.\n\n"
            "Fix it by adding the missing entry, following the exact style of the neighbouring "
            "entries in that file (same shape, a sensible title, an icon that is ALREADY "
            "imported at the top of the file, and a matching category).\n\n"
            "Constraints:\n"
            "- Make the MINIMAL change needed. Do not reformat the file.\n"
            "- Do NOT edit evals/, its datasets, or "
            "`evals/datasets/tool_contract_baseline.json` to silence the check. The eval is the "
            "specification. Fix the source instead.\n"
            "- You do not need to commit; leaving the change in the working tree is fine."
        ),
    },
}


def _capture_failure(suite: str, ws: Path) -> str:
    """Run the suite on the HOST and return its output, to paste into the brief.

    The coder cannot reproduce a failure itself: the sandbox has none of the repository's
    Python dependencies, so `python -m evals.runner` raises ModuleNotFoundError no matter what
    (observed live — the first rung-1 run burned its whole budget retrying it). So the harness
    plays task-framer, exactly as the chad_code skill prescribes: gather the context the coder
    cannot reach, and hand it over distilled.

    The argv MUST match the bridge's `_SUITES` entry for this suite, so the failure baked into
    the brief is the SAME one the bridge later re-runs to verify — otherwise the coder would be
    fixing a different failure than the one it's graded against.
    """
    env = {**os.environ, "PYTHONPATH": "."}
    if suite in _LIVE_SUITES:
        env["CHAD_SERVER_URL"] = _dev_server_url()  # dev Brain only — never prod :8767
    proc = subprocess.run(
        [sys.executable, "-m", "evals.runner", *_SUITE_ARGV[suite]],
        cwd=str(ws), env=env,
        capture_output=True, text=True, errors="replace", timeout=1800,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        print(f"[probe] WARNING: {suite} PASSES on the host — there is nothing to fix")
    tail = "\n".join(out.strip().splitlines()[-60:])
    brief = f"Failure output from `evals.runner {suite}` (exit {proc.returncode}):\n\n```\n{tail}\n```"

    # Attach the full per-case trajectories for the failing cases — the model's reasoning, its
    # tool calls, the plan. For a live failure this is the real signal: it shows WHERE the turn
    # went wrong (which is usually in the thinking), not just that an assertion tripped. Free
    # lanes (tool_contract) write no transcripts, so this is a no-op there.
    try:
        from evals.core import read_latest_transcripts

        transcripts = read_latest_transcripts(ws)
        if transcripts:
            brief += "\n\n" + transcripts
    except Exception as exc:  # noqa: BLE001 — transcript attach is best-effort
        print(f"[probe] transcript read failed (non-fatal): {type(exc).__name__}: {exc}")
    return brief


# Suite key -> argv for `python -m evals.runner`. MUST stay in sync with the bridge's `_SUITES`
# (evals/chad_code_bridge.py): the probe captures the failure with these, the bridge verifies the
# fix with those, and a mismatch means the coder is graded against a different eval than it saw.
_SUITE_ARGV = {
    "tool_contract": ["--tool-contract"],
    "tool_selection": ["--suite", "tool_selection"],
    "prompt_audit": ["--prompt-audit"],
    # Live lanes — capture against the dev Brain (see _capture_failure). Without these entries a
    # live rung KeyError'd here before it could ever dispatch (rung 3 had never run).
    "smoke": ["--suite", "smoke", "--live"],
    "workflows": ["--suite", "workflows", "--workflow"],
}


RUNGS[3] = {
    "title": "chad-self-eval rung 3 (live-lane failure)",
    "suite": "smoke",
    # Live lanes are slow (a real Chad turn per case) and a live failure rarely has a one-line
    # answer, so give the coder real room. Refinement retries share this budget.
    "max_iterations": 60,
    "attempts": 3,
    "task": (
        "A LIVE harness eval is failing. The failure output is reproduced below.\n\n"
        "You CANNOT run this eval yourself: it drives a real Chad server turn, and this sandbox "
        "has none of the repository's Python dependencies (`python -m evals.runner` only ever "
        "raises ModuleNotFoundError). Do not spend turns trying. Verification runs automatically "
        "after you finish, on the host.\n\n"
        "{failure}\n\n"
        "Diagnose the root cause in the source and make a MINIMAL, targeted fix.\n\n"
        "Where this kind of failure usually lives:\n"
        "- tool/subagent selection: `src/infrastructure/tools/selection/`, "
        "`src/infrastructure/tools/delegate_routing.py`, `src/infrastructure/tools/registry.py`\n"
        "- orchestration/routing: `src/core/orchestration/` (nodes/, turn/)\n"
        "- prompts: `prompts/persona/<id>.md` — these are plain files and ARE the live prompts\n\n"
        "Constraints:\n"
        "- Do NOT edit evals/, its datasets, or any assertion to make the eval pass. The eval is "
        "the specification; changing it is a failed run, not a fix. If you genuinely believe the "
        "eval is wrong, say so explicitly and stop rather than editing it.\n"
        "- Prefer the smallest change that addresses the root cause over a broad refactor.\n"
        "- You do not need to commit; leaving changes in the working tree is fine."
    ),
}


async def _dispatch(cfg: dict, workspace: str, bridge: str) -> str:
    from src.tools.dev.openhands_dispatch import OpenHandsDispatchTool

    os.environ["CHAD_EVAL_SUITE"] = cfg["suite"]
    os.environ["CHAD_EVAL_BRIDGE_URL"] = bridge
    os.environ["CHAD_EVAL_MAX_ATTEMPTS"] = str(cfg.get("attempts", 3))

    tool = OpenHandsDispatchTool()
    res = await tool.execute(
        task=cfg["task"],
        title=cfg["title"],
        workspace=workspace,
        max_iterations=cfg["max_iterations"],
    )
    if not res.get("success"):
        raise SystemExit(f"dispatch failed: {res}")
    job_id = res.get("job_id") or res.get("data", {}).get("job_id")
    print(f"[probe] dispatched job_id={job_id}")
    return job_id


def _container(job_id: str) -> str:
    return f"chad-code-{job_id}"


def _logs(job_id: str, tail: int = 400) -> str:
    out = subprocess.run(
        ["docker", "logs", "--tail", str(tail), _container(job_id)],
        capture_output=True, text=True, errors="replace",
    )
    return (out.stdout or "") + (out.stderr or "")


def _status(job_id: str) -> str:
    out = subprocess.run(
        ["docker", "inspect", _container(job_id), "--format", "{{.State.Status}}"],
        capture_output=True, text=True,
    )
    return (out.stdout or "").strip()


def _watch(job_id: str, timeout_s: int) -> str:
    """Poll until the container exits, surfacing critic/bridge milestones as they happen."""
    started = time.monotonic()
    seen: set[str] = set()
    while time.monotonic() - started < timeout_s:
        logs = _logs(job_id, tail=1500)
        for line in logs.splitlines():
            key = line.strip()
            if not key or key in seen:
                continue
            # The lines that tell us whether the LOOP is working (as opposed to the model).
            if any(m in key for m in (
                "CHAD_EVAL_CRITIC", "CHAD_DISPATCH_START", "CHAD_DISPATCH_DONE",
                "CHAD_DISPATCH_ERROR", "CHAD_CONDENSER", "CHAD_SUBAGENTS", "CHAD_SKILLS",
            )):
                seen.add(key)
                print(f"[probe] {key}")
        state = _status(job_id)
        if state and state != "running":
            print(f"[probe] container {state} after {time.monotonic() - started:.0f}s")
            return _logs(job_id, tail=2000)
        time.sleep(10)
    print(f"[probe] TIMEOUT after {timeout_s}s")
    return _logs(job_id, tail=2000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rung", type=int, default=0, choices=sorted(RUNGS))
    ap.add_argument("--workspace", default=_DEFAULT_WS)
    ap.add_argument("--bridge", default=_BRIDGE)
    ap.add_argument("--timeout", type=int, default=2400)
    args = ap.parse_args()

    cfg = RUNGS[args.rung]
    if not args.workspace:
        raise SystemExit(
            "no workspace: set CHAD_CODE_WORKSPACE (the coder's own Chad clone) or pass "
            "--workspace <path>. This is the clone the coder edits and the eval runs against — "
            "NOT the host checkout."
        )
    ws = Path(args.workspace)
    if not (ws / "evals").is_dir():
        raise SystemExit(f"{ws} is not a Chad checkout")

    if "seed" in cfg:
        path, removed = cfg["seed"](ws)
        print(f"[probe] seeded failure in {path}\n[probe] removed line: {removed.strip()}")

    # Bake the real failure into the brief when the task asks for it.
    if "{failure}" in cfg["task"]:
        print(f"[probe] capturing {cfg['suite']} failure on the host...")
        cfg = {**cfg, "task": cfg["task"].replace("{failure}", _capture_failure(cfg["suite"], ws))}

    print(f"[probe] rung {args.rung}: {cfg['title']}")
    print(f"[probe] workspace={ws} suite={cfg['suite']} bridge={args.bridge}")

    job_id = asyncio.run(_dispatch(cfg, str(ws), args.bridge))
    logs = _watch(job_id, args.timeout)

    print("\n===== TAIL =====")
    print("\n".join(logs.splitlines()[-60:]))
    print(f"\n[probe] job_id={job_id}  (docker logs {_container(job_id)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
