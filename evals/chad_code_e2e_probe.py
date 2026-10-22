"""chad_code_e2e_probe.py — end-to-end reliability probe for Chad Code (epic #569, issue #573).

Drives the REAL `dispatch_coding_task` / `check_coding_task` tools (src/tools/dev/
openhands_dispatch.py) against a live Docker + LM Studio, so it exercises the actual harness
Chad ships — model-ensure, the detached container, the headless SDK runner, and the poll/report
path — not a hand-rolled `docker run`.

It is deliberately an *escalating* probe: start with a trivial single-file game, and only move
up to harder multi-file / full-stack tasks once the easy one passes. Each run reports WHERE it
broke down (model never loaded, container failed to launch, agent stalled / asked a question with
no one to answer, tools never fired, files never written, exceeded max_iter, ...).

Prereqs (all must be up — this is a LIVE probe, no mocks):
  - Docker Desktop running, `docker.openhands.dev/openhands/openhands:latest` present.
  - LM Studio serving on :1234 with the coder model DOWNLOADED (it need not be pre-loaded —
    the dispatch tool's ensure step loads it; that load is itself part of what we measure).

Run (from repo root, with the repo venv):
    PYTHONPATH=. python -m evals.chad_code_e2e_probe --preset game
    PYTHONPATH=. python -m evals.chad_code_e2e_probe --preset todo --max-iter 60 --timeout 2400
    PYTHONPATH=. python -m evals.chad_code_e2e_probe --task "Build ..." --title custom

Reports land in evals/runs/chad_code/<job_id>.{json,log}.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.tools.dev import openhands_dispatch as ohd
from src.tools.dev.openhands_dispatch import CheckCodingTaskTool, OpenHandsDispatchTool
from evals.chad_code_verification import (
    capture_fixbugs_fixture, verification_exit_code, verify_fixbugs,
)
from evals.chad_code_run_controls import validate_controls, run_provenance

# ---------------------------------------------------------------------------
# Escalating task presets. Each brief is FULLY self-contained (constraints +
# how-to-verify) because the headless path is single-shot: Chad frames one
# prompt and hands off — there is no back-and-forth to clarify anything.
# ---------------------------------------------------------------------------
# A deliberately buggy library + a CORRECT failing test suite. The edit-existing-code eval
# seeds these into the workspace, then tasks the agent to make the tests pass by fixing the
# SOURCE only — the read-diagnose-surgically-edit skill none of the greenfield tiers exercise.
_BUGGY_MATHLIB = '''\
"""Small math utilities. NOTE: several functions contain bugs."""


def factorial(n):
    if n < 0:
        raise ValueError("negative")
    result = 1
    for i in range(1, n):  # bug: excludes n
        result *= i
    return result


def is_prime(n):
    if n < 2:
        return True  # bug: 0, 1, and negatives are not prime
    for i in range(2, n):
        if n % i == 0:
            return False
    return True


def divide(a, b):
    return a / b  # bug: raises ZeroDivisionError, not ValueError, on b == 0


def gcd(a, b):
    while b:
        a, b = b, a % b
    return b  # bug: returns 0


def fib(n):
    if n < 0:
        raise ValueError("negative")
    a, b = 0, 1
    for _ in range(n + 1):  # bug: one iteration too many
        a, b = b, a + b
    return a
'''

_MATHLIB_TESTS = '''\
"""Correct test suite - DO NOT MODIFY. Fix mathlib.py so these pass."""
import unittest

import mathlib


class TestMathlib(unittest.TestCase):
    def test_factorial(self):
        self.assertEqual(mathlib.factorial(0), 1)
        self.assertEqual(mathlib.factorial(5), 120)

    def test_factorial_negative(self):
        with self.assertRaises(ValueError):
            mathlib.factorial(-1)

    def test_is_prime(self):
        self.assertFalse(mathlib.is_prime(0))
        self.assertFalse(mathlib.is_prime(1))
        self.assertTrue(mathlib.is_prime(2))
        self.assertTrue(mathlib.is_prime(13))
        self.assertFalse(mathlib.is_prime(15))

    def test_divide(self):
        self.assertEqual(mathlib.divide(10, 2), 5)

    def test_divide_by_zero(self):
        with self.assertRaises(ValueError):
            mathlib.divide(1, 0)

    def test_gcd(self):
        self.assertEqual(mathlib.gcd(12, 8), 4)
        self.assertEqual(mathlib.gcd(17, 5), 1)

    def test_fib(self):
        self.assertEqual(mathlib.fib(0), 0)
        self.assertEqual(mathlib.fib(1), 1)
        self.assertEqual(mathlib.fib(7), 13)


if __name__ == "__main__":
    unittest.main()
'''

PRESETS: Dict[str, Dict[str, Any]] = {
    # Explore-and-report — the hardest probe: a LARGE real codebase (read-only at /repo) and NO
    # verification signal (a report, not a test). Hits both remaining unproven breakpoints at
    # once — context scale + "can't run something to know it's right". Pass --reference <dir>.
    "explore": {
        "title": "explore-codebase",
        "task": (
            "A real codebase is mounted READ-ONLY at /repo. You CANNOT modify it (don't try to "
            "edit or write there). Your writable working directory is /workspace.\n"
            "Goal: explore /repo and produce a useful engineering report for someone building a "
            "different Python AI-assistant project ('Chad') who wants to learn from this codebase.\n"
            "How to explore (IMPORTANT): the `codegraph_*` graph tools are indexed over /repo — use "
            "them as your PRIMARY way to understand the CODE, not raw file crawling. Start with "
            "`codegraph_files` for the layout, then `codegraph_explore` (pass symbol names or a "
            "natural-language question) to read the substantial modules — it returns the real "
            "source, so you rarely need to open files by hand. Use `codegraph_callers`/"
            "`codegraph_callees`/`codegraph_impact` to trace how pieces connect. Use `find`/`grep`/"
            "`cat` ONLY for specific non-code things the graph doesn't cover (READMEs, config, data "
            "files) — not for general code exploration. Don't read every file.\n"
            "Do NOT over-deliberate: once you understand the architecture and have identified the "
            "key modules, WRITE `/workspace/REPORT.md` promptly rather than continuing to think.\n"
            "Write your report to `/workspace/REPORT.md` with these sections:\n"
            "1. What the project is + its overall architecture (entry points, main packages).\n"
            "2. The best-engineered / most reusable parts — specific modules, patterns, or "
            "techniques worth studying, WITH their real file paths in /repo.\n"
            "3. Concrete recommendations: what to adapt or reuse in the Chad project, and why.\n"
            "Be specific and cite real paths/components from /repo — no generic advice. When "
            "`/workspace/REPORT.md` is written and complete, stop."
        ),
    },
    # Edit-existing-code — the real untested breakpoint: an existing (buggy) codebase the agent
    # must read, diagnose, and surgically fix. Seeded into the workspace before dispatch.
    "fixbugs": {
        "title": "fix-mathlib-bugs",
        "seed": {"mathlib.py": _BUGGY_MATHLIB, "test_mathlib.py": _MATHLIB_TESTS},
        "task": (
            "The workspace (/workspace) already contains `mathlib.py` and `test_mathlib.py`. "
            "Running `python -m unittest test_mathlib.py` currently FAILS — `mathlib.py` has "
            "several bugs.\n"
            "Your job: make ALL tests pass by fixing ONLY `mathlib.py`. Do NOT modify "
            "`test_mathlib.py` (the tests are correct). Do not rewrite the whole file — read it, "
            "find each bug, and fix it surgically.\n"
            "Workflow: run `python -m unittest test_mathlib.py -v`, read the failures, fix the "
            "relevant function in `mathlib.py`, and re-run until every test passes. Then stop."
        ),
    },
    # Tier 1 — trivial, single file, no deps. The "can the harness produce a
    # real runnable artifact at all" smoke test.
    "game": {
        "title": "snake-game",
        "task": (
            "Build a small browser game as a SINGLE self-contained file named `snake.html` in "
            "the current directory (/workspace).\n"
            "Requirements:\n"
            "- Pure HTML + CSS + JavaScript in ONE file. No external libraries, no build step, "
            "no network calls.\n"
            "- Classic Snake: a snake on a grid that grows when it eats food, controlled with "
            "the arrow keys.\n"
            "- Display the current score. On collision with a wall or itself, show 'Game Over' "
            "and let the player restart by pressing Space.\n"
            "- It must run correctly by just opening the file in a browser.\n"
            "Verify before finishing: confirm `snake.html` exists in /workspace and that it "
            "contains a <canvas> (or grid), an arrow-key handler, and score + game-over logic. "
            "Then stop."
        ),
    },
    # Tier 3 — HARD: multi-module app, validation edge cases, 8+ tests. Meant to run
    # long and force repeated test-fix cycles — probes where the *harness* (not just the
    # model) breaks: max_iter, context growth, fix-loops, our missing container timeout.
    "hard": {
        "title": "expense-tracker-cli",
        "task": (
            "Build a command-line expense tracker in the current directory (/workspace) using "
            "ONLY the Python standard library — NO pip installs. Structure it as MULTIPLE "
            "modules:\n"
            "- `storage.py`: load/save expenses to `expenses.json`; each expense = "
            "{id, date 'YYYY-MM-DD', amount float, category str, note str}. A missing/empty/"
            "corrupt file must return an empty list, never crash.\n"
            "- `tracker.py`: core logic — add_expense (validate: amount > 0, valid date, "
            "non-empty category), list_expenses (optional filter by category and/or month "
            "'YYYY-MM'), monthly_summary (total per category for a given month).\n"
            "- `cli.py`: an argparse CLI with subcommands: `add --date --amount --category "
            "--note`, `list [--category] [--month]`, `report --month` (per-category totals + "
            "grand total), and `export --out FILE` (write all expenses to CSV).\n"
            "- `test_tracker.py`: a stdlib `unittest` suite with AT LEAST 8 tests covering: a "
            "valid add, rejecting a zero/negative amount, rejecting an invalid date, filtering "
            "by category, filtering by month, the monthly-summary math, CSV export contents, "
            "and graceful handling of a missing data file. ALL tests must pass.\n"
            "- `README.md`: usage for each subcommand.\n"
            "Verify before finishing: run `python -m unittest test_tracker.py -v` and confirm "
            "ALL tests pass. If any fail, fix and re-run until green. Then stop."
        ),
    },
    # Tier 2 — multi-file, a stack, and a real passing test. Escalate here only
    # after Tier 1 passes.
    "todo": {
        "title": "todo-fullstack",
        "task": (
            "Build a minimal but complete full-stack TODO app in the current directory "
            "(/workspace), using only the Python standard library — NO pip installs.\n"
            "Requirements:\n"
            "- `server.py`: an http.server-based JSON API supporting GET /todos, POST /todos "
            "(add), and POST /todos/<id>/toggle (mark done). Persist todos to a `todos.json` "
            "file so they survive restarts.\n"
            "- `index.html`: a single static page (vanilla JS, no frameworks) that lists todos, "
            "adds a todo, and toggles done, talking to the API.\n"
            "- `test_server.py`: a stdlib `unittest` test that starts the API in-process (or "
            "against a temp file), adds a todo, toggles it, and asserts the persisted state. "
            "It must pass.\n"
            "- `README.md`: how to run the server and open the page.\n"
            "Verify before finishing: run `python -m unittest test_server.py` and confirm it "
            "passes. If it fails, fix it and re-run until green. Then stop."
        ),
    },
}


def _report_dir() -> Path:
    d = Path(__file__).resolve().parents[1] / "evals" / "runs" / "chad_code"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _full_logs(container: str) -> str:
    try:
        p = subprocess.run(
            ["docker", "logs", container],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
        return (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
    except Exception as e:  # noqa: BLE001
        return f"<could not read full logs: {e}>"


def _analyze_log(logs: str) -> Dict[str, Any]:
    """Post-run forensics so a run tells us WHY it went the way it did (issue #582): which tools the
    agent actually used (crucially — did it touch codegraph at all, or fall back to raw terminal?),
    the input-token spend, and the model's last few thoughts. The thought trail is the single most
    useful signal when a run stalls or loops (e.g. the think-loop-to-timeout we hit), so the runner
    now streams `CHAD_THOUGHT:` lines and we surface the tail of them here."""
    tool_usage: Dict[str, int] = {}
    for m in re.finditer(r"kind='(\w+?)Action'", logs):  # TerminalAction, FileEditorAction, MCPTool...
        name = m.group(1)
        tool_usage[name] = tool_usage.get(name, 0) + 1
    # codegraph calls surface as generic `kind='MCPToolAction'` — the tool NAME isn't in that line
    # unless the runner tagged it (newer streamer prints the codegraph_* name). Count both: a named
    # codegraph_* ActionEvent if present, else fall back to the MCPToolAction count (on an explore
    # task the MCP calls ARE codegraph — context7 is docs-lookup, not repo exploration).
    named = len(re.findall(r"ActionEvent[^\n]*codegraph_codegraph_\w+", logs))
    mcp_calls = tool_usage.get("MCPTool", 0)
    codegraph_calls = named or mcp_calls
    # A call attempt != a useful call. codegraph errors (e.g. "not initialized" on a bare read-only
    # mount — the #582 bug) show as an ❌ result; count them so `codegraph_ok` reflects calls that
    # actually returned graph data, not just attempts. Also surface whether the index got built.
    cg_errors = len(re.findall(r"Tool: codegraph_codegraph_\w+\s*\n\s*Result:\s*\n\s*❌", logs))
    cg_errors = max(cg_errors, len(re.findall(r"CodeGraph not initialized|No CodeGraph project", logs)))
    codegraph_ok = max(0, codegraph_calls - cg_errors)
    codegraph_indexed = ("CHAD_CODEGRAPH_INDEXED" in logs) and ("CHAD_CODEGRAPH_INIT_FAILED" not in logs)
    tokens = re.findall(r"input\s+([\d.]+K?)\b", logs)
    # Model reasoning shows up two ways depending on the model/config: a dedicated CHAD_THOUGHT:
    # line (possibly a structured TextContent repr we unwrap), or embedded as `thought='...'` in an
    # ActionEvent repr. Harvest both, drop empties, so the thought trail is human-readable — this is
    # what makes a stalled/looping run diagnosable (#582).
    thoughts: List[str] = []
    for raw in re.findall(r"CHAD_THOUGHT:\s*(.+)", logs):
        m = re.search(r"text=['\"](.*?)['\"]", raw)
        text = (m.group(1) if m else raw).strip()
        if text and "text=''" not in raw and text != "[]":
            thoughts.append(text[:400])
    for text in re.findall(r"thought=['\"]([^'\"]{15,}?)['\"]", logs):
        thoughts.append(text.strip()[:400])
    return {
        "tool_usage": tool_usage,
        "codegraph_indexed": codegraph_indexed,
        "codegraph_calls": codegraph_calls,
        "codegraph_errors": cg_errors,
        "codegraph_ok": codegraph_ok,
        "codegraph_used": codegraph_ok > 0,  # USEFUL usage: a call that actually returned graph data
        "mcp_calls": mcp_calls,
        "tokens_input": tokens[-1] if tokens else None,
        "last_thoughts": thoughts[-4:],
    }


def _diagnose(status: str, result: Dict[str, Any], logs: str) -> str:
    """Best-effort 'where did it break down' verdict from status + files + logs."""
    files = result.get("files", [])
    tail = "\n".join(result.get("transcript_tail", []))
    if status == "finished" and files:
        return "COMPLETED — agent finished; task correctness has not been independently verified."
    if status == "finished" and not files:
        return ("SUSPECT — agent reported done but wrote NO files. Likely tools never fired "
                "(check native_tool_calling / Hermes parsing) or it 'finished' without acting.")
    if status == "running":
        return ("TIMEOUT — still running when the probe gave up. Model load stall, a slow "
                "agent loop, or a stuck/looping run. Raise --timeout or lower task scope.")
    # failed
    if "CHAD_DISPATCH_ERROR" in logs:
        return "FAILED — runner raised (see CHAD_DISPATCH_ERROR line in logs)."
    if "max_iter" in logs.lower() or "iteration" in logs.lower():
        return "FAILED/STALLED — likely hit max_iterations before finishing. Raise --max-iter."
    if not logs.strip():
        return ("FAILED — no container logs at all. Model may never have become available, or "
                "the container exited before the runner produced output.")
    return "FAILED — see logs tail for the last agent action before it stopped."


async def _main() -> int:
    ap = argparse.ArgumentParser(description="Chad Code end-to-end reliability probe (#573).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--preset", choices=sorted(PRESETS), help="a built-in escalating task")
    g.add_argument("--task", help="a custom task brief (self-contained)")
    ap.add_argument("--title", default=None, help="short job label")
    ap.add_argument("--workspace", default=None, help="existing host dir (default: fresh temp)")
    ap.add_argument("--reference", default=None, help="host codebase to mount READ-ONLY at /repo")
    ap.add_argument("--model", default=None, help="override coder model id (LiteLLM form)")
    ap.add_argument("--max-iter", type=int, default=30, help="agent max iterations (default 30)")
    ap.add_argument("--timeout", type=int, default=1200, help="poll timeout seconds (default 1200)")
    ap.add_argument("--poll", type=int, default=20, help="seconds between status polls")
    ap.add_argument("--no-codegraph", action="store_true",
                    help="disable the codegraph MCP even with a --reference (A/B control, #582)")
    args = ap.parse_args()

    if args.model:
        # Dispatch, preflight and model residency all consult this configuration.
        configured = ohd._cfg
        ohd._cfg = lambda key, default=None: args.model if key == "openhands_coder_model" else configured(key, default)

    if args.no_codegraph:
        # Probe-only override: force the codegraph gate off so an explore run uses the base image
        # and no graph tools — the control arm of the codegraph A/B (#582).
        # Wrap the active config so an explicit model override is preserved.
        _orig_cfg = ohd._cfg
        ohd._cfg = (lambda key, default=None, _o=_orig_cfg:  # noqa: SLF001 — probe-only override
                    False if key == "openhands_codegraph_mcp" else _o(key, default))

    if args.preset:
        task = PRESETS[args.preset]["task"]
        title = args.title or PRESETS[args.preset]["title"]
    else:
        task = args.task
        title = args.title or "custom-task"

    # Edit-existing-code presets ship a `seed`: write those files into a fresh workspace before
    # dispatch so the agent must read + modify existing code rather than start from empty.
    seed = PRESETS.get(args.preset or "", {}).get("seed")
    if seed and not args.workspace:
        seed_ws = Path(tempfile.gettempdir()) / f"chad-code-seed-{uuid.uuid4().hex[:8]}"
        seed_ws.mkdir(parents=True, exist_ok=True)
        for fname, content in seed.items():
            (seed_ws / fname).write_text(content, encoding="utf-8")
        args.workspace = str(seed_ws)
        print(f"seeded workspace {seed_ws} with: {', '.join(seed)}")

    baseline_source = protected_tests = None
    controls = None
    if args.preset == "fixbugs":
        try:
            baseline_source, protected_tests = capture_fixbugs_fixture(
                Path(args.workspace), _MATHLIB_TESTS.encode("utf-8"))
        except (OSError, ValueError) as exc:
            print(f"INVALID FIXBUGS WORKSPACE: {exc}")
            return 2
        controls = validate_controls(baseline_source, protected_tests)
        if not controls["ready"]:
            rd = _report_dir()
            rd.mkdir(parents=True, exist_ok=True)
            evidence = rd / f"preflight-{uuid.uuid4().hex[:8]}.json"
            evidence.write_text(json.dumps({"outcome": "invalid_fixture_or_grader", "controls": controls}, indent=2), encoding="utf-8")
            print(f"FIXBUGS PREFLIGHT FAILED: baseline must fail and known fix must pass. Evidence: {evidence}")
            return 2

    dispatch = OpenHandsDispatchTool()
    check = CheckCodingTaskTool()

    print(f"=== chad_code_e2e_probe: preset={args.preset or '(custom)'} title={title} ===")
    selected_model = ohd._cfg("openhands_coder_model", ohd._DEFAULT_MODEL)
    print(f"selected model: {selected_model}   max_iter={args.max_iter}   timeout={args.timeout}s")
    print(f"task:\n{task}\n")
    print("Dispatching (this also ensures the coder model is resident — may take a minute on a "
          "cold load)...")

    reference = args.reference or PRESETS.get(args.preset or "", {}).get("reference")
    if reference:
        print(f"reference codebase (read-only /repo): {reference}")

    t0 = time.time()
    disp = await dispatch.execute(task=task, title=title, workspace=args.workspace,
                                  reference=reference, max_iterations=args.max_iter)
    if not disp.get("success"):
        print(f"DISPATCH FAILED: {disp.get('error')}")
        return 1

    res = disp["result"]
    job_id, container, workspace = res["job_id"], res["container"], res["workspace"]
    print(f"dispatched: job_id={job_id} container={container}")
    print(f"workspace: {workspace}\n")

    last_status = "running"
    last_result: Dict[str, Any] = {}
    deadline = t0 + args.timeout
    while time.time() < deadline:
        await asyncio.sleep(args.poll)
        chk = await check.execute(job_id=job_id)
        if not chk.get("success"):
            print(f"  check error: {chk.get('error')}")
            continue
        last_result = chk["result"]
        last_status = last_result["status"]
        tail = last_result.get("transcript_tail", [])
        last_line = tail[-1] if tail else "(no transcript yet)"
        elapsed = int(time.time() - t0)
        print(f"  [{elapsed:>4}s] status={last_status:<9} files={len(last_result.get('files', []))} "
              f":: {last_line[:120]}")
        if last_status in ("finished", "failed"):
            break

    dur = time.time() - t0
    logs = _full_logs(container)
    verdict = _diagnose(last_status, last_result, logs)
    analysis = _analyze_log(logs)
    verification = None
    if args.preset == "fixbugs":
        verification = verify_fixbugs(Path(workspace), baseline_source, protected_tests,
                                      last_status, last_result.get("exit_code"), container=True)
        verdict = f"{verification.outcome.upper()} — {verification.detail}"

    print("\n================= RESULT =================")
    print(f"job {job_id}  status={last_status}  elapsed={dur:.0f}s")
    print(f"files ({len(last_result.get('files', []))}): {last_result.get('files', [])}")
    print(f"workspace: {workspace}")
    print("\n--- forensics (why) ---")
    print(f"tools used:      {analysis['tool_usage'] or '(none captured)'}")
    print(f"codegraph:       indexed={analysis['codegraph_indexed']}  "
          f"used={analysis['codegraph_used']}  "
          f"({analysis['codegraph_ok']} ok / {analysis['codegraph_errors']} err "
          f"of {analysis['codegraph_calls']} call(s))")
    print(f"input tokens:    {analysis['tokens_input'] or 'n/a'}")
    print("last thoughts:")
    for t in analysis["last_thoughts"] or ["(none captured)"]:
        print(f"  • {t}")
    print("\n--- transcript tail ---")
    print("\n".join(last_result.get("transcript_tail", [])) or "(none)")
    print("\n================= VERDICT =================")
    print(verdict)

    # Persist the full report for later comparison across models / max_iter.
    rd = _report_dir()
    report = {
        "job_id": job_id, "preset": args.preset, "title": title,
        "model": res.get("model", selected_model), "max_iter": args.max_iter,
        "status": last_status, "elapsed_s": round(dur, 1),
        "files": last_result.get("files", []), "workspace": workspace,
        "verdict": verdict, "analysis": analysis, "task": task,
        "provenance": run_provenance(container, Path(__file__).resolve().parents[1], task),
    }
    if controls is not None:
        report["controls"] = controls
    if verification is not None:
        report["verification"] = asdict(verification)
        (rd / f"{job_id}.verification.log").write_text(
            verification.stdout + verification.stderr, encoding="utf-8")
    (rd / f"{job_id}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (rd / f"{job_id}.log").write_text(logs, encoding="utf-8")
    print(f"\nreport: {rd / (job_id + '.json')}")
    print(f"logs:   {rd / (job_id + '.log')}")
    return verification_exit_code(last_status, verification)


async def main() -> int:
    # CLI calls may repeat in one process; do not leak either temporary override.
    original_cfg = ohd._cfg
    try:
        return await _main()
    finally:
        ohd._cfg = original_cfg


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
