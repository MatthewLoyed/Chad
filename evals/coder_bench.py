r"""coder_bench.py — SWE-bench-style outcome eval for Chad's coding harness (issue #514).

Backend-pluggable (`CoderBackend` protocol) so this doubles as the #447 bake-off tool: run
the same real historical-issue tasks through the in-house LangGraph `code_agent` and (once
#447's `run_openhands_task` wrapper lands) an OpenHands-delegated backend, and compare
resolved-rate + cost side by side instead of guessing which harness to bet on.

Each task = one real closed issue from THIS repo's history: a `base_commit` (just before the
fix), a `prompt` (the issue text), and FAIL_TO_PASS / PASS_TO_PASS pytest commands (SWE-bench's
own methodology — execution-based grading, not string-matching a reference patch). For each
task:
  1. `git worktree add` a disposable checkout at `base_commit`.
  2. Spawn a throwaway `src/remote/server.py` instance with `cwd=<worktree>` (this is what
     scopes `FileWriter`'s write-whitelist to the worktree, not the real repo — see
     `src/tools/filesystem/file_writer.py:70-73`) on a random free port (never :8767 — the
     server's auto-update loop is already gated to prod-only, so a non-8767 instance is inert
     there too — see `src/remote/server.py:320-324`).
  3. Drive the task prompt through the full supervisor turn via `evals.live.probe_workflow`
     (the only path that reaches `code_agent` — it's a handoff-only subgraph, there's no
     direct call API) and capture the `LiveTrace` (reasoning, tool calls, duration, tokens).
  4. `git diff` the worktree for the patch, run FAIL_TO_PASS/PASS_TO_PASS as subprocesses,
     classify the outcome, tear the worktree + server down.

NEVER touches the production Brain (:8767) — every run is a disposable subprocess on a
random port, in a disposable git worktree, torn down after each task.

Run:
  $env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.coder_bench
  $env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.coder_bench --backends inhouse,openhands
  $env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.coder_bench --task fix_433_selector_assertions

Exit code: 0 = every ran task resolved, 1 = any unresolved/regression/no-op/backend-error.
Not wired into CI by default — needs LM Studio's qwen3-coder-30b loaded (see model-safety
note in evals/REFERENCE.md) and a real git history to check out against.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

# Windows console default codepage can't encode some report glyphs — see session_isolation_probe.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evals.live as live_mod
from evals.core import PROJECT_ROOT, RUNS_DIR
from evals.live import LiveTrace, load_auth_token, server_is_up
from src.shared.fs_sandbox import ENV_VAR as FS_SANDBOX_ENV

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
DEFAULT_DATASET = DATASETS_DIR / "coder_tasks.jsonl"
# Outside the repo tree entirely — avoids nesting a disposable worktree inside whatever
# worktree this script itself happens to be running from.
SCRATCH_ROOT = Path(tempfile.gettempdir()) / "chad_coder_bench"

CODER_MODEL_NOTE = (
    "code_agent pins qwen/qwen3-coder-30b (src/core/orchestration/agents/factory.py) — make "
    "sure it's already loaded in LM Studio before running this; JIT-loading a 30B model on "
    "top of others starves VRAM (see evals/AGENTS.md model-safety note)."
)


# ── Task model ────────────────────────────────────────────────────────────────


@dataclass
class CoderTask:
    id: str
    issue_ref: str
    base_commit: str
    prompt: str
    fail_to_pass: List[str]
    pass_to_pass: List[str] = field(default_factory=list)
    timeout_s: int = 300

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "CoderTask":
        return CoderTask(
            id=d["id"], issue_ref=d.get("issue_ref", ""), base_commit=d["base_commit"],
            prompt=d["prompt"], fail_to_pass=d.get("fail_to_pass", []),
            pass_to_pass=d.get("pass_to_pass", []), timeout_s=d.get("timeout_s", 300),
        )


def load_tasks(path: Path, only_id: Optional[str] = None) -> List[CoderTask]:
    tasks: List[CoderTask] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        d = json.loads(line)
        if only_id and d["id"] != only_id:
            continue
        tasks.append(CoderTask.from_json(d))
    return tasks


# ── Backend protocol ─────────────────────────────────────────────────────────


@dataclass
class CoderResult:
    backend: str
    task_id: str
    diff: str = ""
    trace: Optional[LiveTrace] = None
    duration_s: float = 0.0
    total_tokens: int = 0
    error: Optional[str] = None


class CoderBackend(Protocol):
    name: str

    async def run_task(self, task: CoderTask, worktree_dir: Path) -> CoderResult: ...


def _git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Worktree:
    """One disposable git checkout at a task's base_commit, torn down on exit."""

    def __init__(self, task_id: str, base_commit: str):
        self.task_id = task_id
        self.base_commit = base_commit
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.path = SCRATCH_ROOT / f"{task_id}_{uuid.uuid4().hex[:8]}"

    def __enter__(self) -> Path:
        r = _git(["worktree", "add", "--detach", str(self.path), self.base_commit], cwd=PROJECT_ROOT)
        if r.returncode != 0:
            raise RuntimeError(f"git worktree add failed for {self.task_id}: {r.stderr.strip()}")
        return self.path

    def __exit__(self, *exc) -> None:
        _git(["worktree", "remove", "--force", str(self.path)], cwd=PROJECT_ROOT)
        # Belt-and-suspenders: `worktree remove` can refuse if a stray server process still
        # holds an open handle in the directory (e.g. AdHocServer.stop() didn't fully exit yet).
        shutil.rmtree(self.path, ignore_errors=True)


class AdHocServer:
    """A throwaway `src/remote/server.py` instance, cwd-scoped to one worktree, on a random
    non-8767 port. Never the production Brain — see module docstring."""

    def __init__(self, cwd: Path, port: Optional[int] = None):
        self.cwd = cwd
        self.port = port or _free_port()
        self.proc: Optional[subprocess.Popen] = None

    def start(self, timeout_s: float = 90.0) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = "."
        env["PORT"] = str(self.port)
        # Confine filesystem discovery to this worktree (#566). `cwd` alone does not do it:
        # DiscoveryEngine roots itself at %USERPROFILE% and `fuzzy_find_local` auto-searches
        # Documents/Desktop/Downloads, so a bench turn read a file out of the LIVE production
        # repo instead of its own disposable checkout — silently invalidating the measurement
        # and reaching into real user files. Set on the child's env only, so it can't leak
        # into this process or the production Brain.
        env[FS_SANDBOX_ENV] = str(self.cwd)
        self.proc = subprocess.Popen(
            [sys.executable, "src/remote/server.py"],
            cwd=str(self.cwd), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Retarget the shared eval-live module globals at this ad-hoc instance — both are
        # read at call time (not closed over), so probe_workflow()/server_is_up() below pick
        # this up with no other plumbing. See evals/live.py:25-39.
        live_mod.SERVER_WS = f"ws://localhost:{self.port}/ws"
        live_mod.HEALTH_URL = live_mod._health_url_from_ws(live_mod.SERVER_WS)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if server_is_up():
                return
            if self.proc.poll() is not None:
                raise RuntimeError(f"ad-hoc server on :{self.port} exited early (code {self.proc.returncode})")
            time.sleep(1.0)
        raise RuntimeError(f"ad-hoc server on :{self.port} did not become healthy within {timeout_s}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class InHouseCoderBackend:
    name = "inhouse"

    async def run_task(self, task: CoderTask, worktree_dir: Path) -> CoderResult:
        server = AdHocServer(cwd=worktree_dir)
        try:
            server.start()
        except Exception as e:
            # start() can raise AFTER Popen() already succeeded (e.g. health-check timeout) —
            # stop() here prevents an orphaned server subprocess outliving this task.
            server.stop()
            return CoderResult(backend=self.name, task_id=task.id, error=f"server start failed: {e}")

        token = load_auth_token()
        if not token:
            server.stop()
            return CoderResult(backend=self.name, task_id=task.id,
                                error="no auth token (CHAD_REMOTE_TOKEN / .env)")

        def _has_edit() -> tuple[bool, str]:
            """Observable completion signal: did anything in the worktree actually change?

            `code_agent` would read the target file and end the turn with "Task completed."
            having written nothing, and the harness had no way to notice: agent-lane turns
            carry no plan, so the plan-progress nudge could never fire (#566). `git diff` is
            the bench's own success signal and cannot be satisfied by the model asserting
            it finished.

            Deliberately NOT the FAIL_TO_PASS tests: making the stop condition the graded
            outcome would let the agent tune against the answer key. "A change exists" is
            the weakest honest signal that distinguishes work from narration.
            """
            r = _git(["status", "--porcelain"], cwd=worktree_dir)
            if r.returncode != 0:                     # treated as satisfied — see _evaluate_completion_check
                raise RuntimeError(f"git status failed: {r.stderr.strip()}")
            if r.stdout.strip():
                return True, ""
            return False, "no file in the repository has been modified yet — there is no diff"

        try:
            trace = await live_mod.probe_workflow(
                task.prompt, category="agent", auth_token=token,
                max_rounds=max(6, task.timeout_s // 30),
                completion_check=_has_edit,
            )
        except Exception as e:
            return CoderResult(backend=self.name, task_id=task.id, error=f"probe_workflow raised: {e}")
        finally:
            server.stop()

        diff = _git(["diff"], cwd=worktree_dir).stdout
        return CoderResult(
            backend=self.name, task_id=task.id, diff=diff, trace=trace,
            duration_s=trace.duration_s, total_tokens=trace.total_tokens, error=trace.error,
        )


class OpenHandsCoderBackend:
    """Stub — issue #447 Phase 1 (`run_openhands_task`) isn't built yet. Ships so
    `--backends inhouse,openhands` already has the right shape the moment it lands, with no
    rewrite of the harness/report/comparison-table code needed."""

    name = "openhands"

    async def run_task(self, task: CoderTask, worktree_dir: Path) -> CoderResult:
        raise NotImplementedError(
            "OpenHands backend not available yet — blocked on issue #447 Phase 1 "
            "(run_openhands_task wrapper not built)."
        )


BACKENDS: Dict[str, CoderBackend] = {
    "inhouse": InHouseCoderBackend(),
    "openhands": OpenHandsCoderBackend(),
}


# ── Scoring ───────────────────────────────────────────────────────────────────


@dataclass
class TaskVerdict:
    backend: str
    task_id: str
    outcome: str  # resolved | regression | unresolved | no-op | backend-error
    diff_lines: int
    duration_s: float
    total_tokens: int
    fail_to_pass_ok: Optional[bool]
    pass_to_pass_ok: Optional[bool]
    detail: str = ""


def _normalize_cmd(cmd: str) -> str:
    """`pytest ...` -> `<this venv's python> -m pytest ...` so the test command resolves
    correctly even when the caller invoked coder_bench.py's venv python directly without
    activating the venv (no `pytest` on PATH in that case)."""
    if cmd.startswith("pytest "):
        return f'"{sys.executable}" -m pytest {cmd[len("pytest "):]}'
    return cmd


def _run_tests(cmds: List[str], cwd: Path, timeout_s: int) -> bool:
    """All commands must exit 0. Empty list = vacuously true (no tests specified for that slot).

    Uses shell=True on task-authored `fail_to_pass`/`pass_to_pass` strings — safe only because
    those commands come from the committed, human-reviewed `coder_tasks.jsonl` (same trust level
    as any other repo-tracked code, e.g. a CI workflow step), never from `code_agent`'s output or
    any other untrusted source. Do NOT wire an uncommitted/mined candidate straight into this.
    """
    if not cmds:
        return True
    for cmd in cmds:
        try:
            r = subprocess.run(_normalize_cmd(cmd), shell=True, cwd=str(cwd),
                                capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return False
        if r.returncode != 0:
            return False
    return True


def score(task: CoderTask, result: CoderResult, worktree_dir: Path) -> TaskVerdict:
    if result.error:
        return TaskVerdict(backend=result.backend, task_id=task.id, outcome="backend-error",
                            diff_lines=0, duration_s=result.duration_s, total_tokens=result.total_tokens,
                            fail_to_pass_ok=None, pass_to_pass_ok=None, detail=result.error)

    diff_lines = len(result.diff.splitlines())
    if diff_lines == 0:
        # Say how hard we pushed before giving up. A no-op after three explicit,
        # gap-naming nudges is a much stronger signal than one after a single ask —
        # without the count, the two are indistinguishable in the report.
        nudges = getattr(result.trace, "continue_nudges", 0) or 0
        detail = "empty diff — no edit attempted"
        if nudges:
            detail += f" (after {nudges} completion nudge(s))"
        return TaskVerdict(backend=result.backend, task_id=task.id, outcome="no-op",
                            diff_lines=0, duration_s=result.duration_s, total_tokens=result.total_tokens,
                            fail_to_pass_ok=False, pass_to_pass_ok=None, detail=detail)

    fp_ok = _run_tests(task.fail_to_pass, worktree_dir, task.timeout_s)
    pp_ok = _run_tests(task.pass_to_pass, worktree_dir, task.timeout_s)

    if fp_ok and pp_ok:
        outcome = "resolved"
    elif fp_ok and not pp_ok:
        outcome = "regression"
    else:
        outcome = "unresolved"

    return TaskVerdict(backend=result.backend, task_id=task.id, outcome=outcome, diff_lines=diff_lines,
                        duration_s=result.duration_s, total_tokens=result.total_tokens,
                        fail_to_pass_ok=fp_ok, pass_to_pass_ok=pp_ok)


# ── Orchestration ─────────────────────────────────────────────────────────────


async def run_task_for_backend(backend: CoderBackend, task: CoderTask) -> TaskVerdict:
    print(f"[coder_bench] {backend.name}/{task.id}: worktree @ {task.base_commit[:8]}")
    try:
        wt = Worktree(task.id, task.base_commit)
        with wt as wt_path:
            try:
                result = await backend.run_task(task, wt_path)
            except NotImplementedError as e:
                return TaskVerdict(backend=backend.name, task_id=task.id, outcome="backend-error",
                                    diff_lines=0, duration_s=0.0, total_tokens=0,
                                    fail_to_pass_ok=None, pass_to_pass_ok=None, detail=str(e))
            return score(task, result, wt_path)
    except Exception as e:
        return TaskVerdict(backend=backend.name, task_id=task.id, outcome="backend-error",
                            diff_lines=0, duration_s=0.0, total_tokens=0,
                            fail_to_pass_ok=None, pass_to_pass_ok=None, detail=f"harness error: {e}")


async def run_bench(tasks: List[CoderTask], backend_names: List[str]) -> Dict[str, List[TaskVerdict]]:
    # Sequential (tasks x backends) — code_agent's local model is heavy; running two ad-hoc
    # servers concurrently would contend for the same LM Studio GPU slot. See CODER_MODEL_NOTE.
    results: Dict[str, List[TaskVerdict]] = {}
    for name in backend_names:
        backend = BACKENDS[name]
        verdicts: List[TaskVerdict] = []
        for task in tasks:
            v = await run_task_for_backend(backend, task)
            verdicts.append(v)
            print(f"[coder_bench] {name}/{task.id}: {v.outcome} ({v.detail or 'ok'})")
        results[name] = verdicts
    return results


# ── Report ────────────────────────────────────────────────────────────────────


def render_report(results: Dict[str, List[TaskVerdict]]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"# Coder bench — {ts}", ""]
    for backend, verdicts in results.items():
        resolved = sum(1 for v in verdicts if v.outcome == "resolved")
        total = len(verdicts)
        lines.append(f"## {backend}: {resolved}/{total} resolved")
        lines.append("")
        lines.append("| task | outcome | diff lines | duration_s | tokens | detail |")
        lines.append("|---|---|---|---|---|---|")
        for v in verdicts:
            lines.append(f"| {v.task_id} | {v.outcome} | {v.diff_lines} | {v.duration_s:.1f} | "
                          f"{v.total_tokens} | {v.detail} |")
        lines.append("")
    if len(results) > 1:
        lines.append("## Backend comparison (the #447 bake-off table)")
        lines.append("")
        lines.append("| backend | resolved rate | avg duration_s | avg tokens |")
        lines.append("|---|---|---|---|")
        for backend, verdicts in results.items():
            scored = [v for v in verdicts if v.outcome != "backend-error"]
            rate = sum(1 for v in verdicts if v.outcome == "resolved") / max(1, len(verdicts))
            avg_dur = sum(v.duration_s for v in scored) / max(1, len(scored)) if scored else 0.0
            avg_tok = sum(v.total_tokens for v in scored) / max(1, len(scored)) if scored else 0.0
            lines.append(f"| {backend} | {rate:.0%} | {avg_dur:.1f} | {avg_tok:.0f} |")
        lines.append("")
    return "\n".join(lines)


def persist(results: Dict[str, List[TaskVerdict]]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (RUNS_DIR / "coder_bench_latest.md").write_text(render_report(results), encoding="utf-8")
    json_path = RUNS_DIR / f"coder_bench_{ts}.json"
    json_path.write_text(
        json.dumps({b: [vars(v) for v in vs] for b, vs in results.items()}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[coder_bench] saved: {RUNS_DIR / 'coder_bench_latest.md'}")
    print(f"[coder_bench] saved: {json_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SWE-bench-style outcome eval for code_agent (#514)")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--task", type=str, default=None, help="run only this task id")
    parser.add_argument("--backends", type=str, default="inhouse",
                         help="comma-separated backend names (inhouse,openhands)")
    args = parser.parse_args(argv)

    if not args.dataset.exists():
        print(f"[coder_bench] no dataset at {args.dataset} — nothing to run")
        return 1

    tasks = load_tasks(args.dataset, only_id=args.task)
    if not tasks:
        print(f"[coder_bench] no tasks matched in {args.dataset}")
        return 1

    backend_names = [b.strip() for b in args.backends.split(",") if b.strip()]
    unknown = [b for b in backend_names if b not in BACKENDS]
    if unknown:
        print(f"[coder_bench] unknown backend(s) {unknown} (known: {sorted(BACKENDS)})")
        return 1

    print(f"[coder_bench] {len(tasks)} task(s), backends={backend_names}")
    print(f"[coder_bench] {CODER_MODEL_NOTE}")

    results = asyncio.run(run_bench(tasks, backend_names))
    persist(results)

    any_bad = any(v.outcome != "resolved" for vs in results.values() for v in vs)
    return 1 if any_bad else 0


if __name__ == "__main__":
    sys.exit(main())
