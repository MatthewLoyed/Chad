"""
runner.py — the one command an agent runs to evaluate the harness.

    # FREE deterministic lane (no LLM) — the fast inner loop
    python -m evals.runner --suite tool_selection

    # Add the LLM judge (cloud by default) for semantic grading
    python -m evals.runner --suite tool_selection --judge

    # Loop cheaply on the local model (Gemma / Qwen 3.6)
    python -m evals.runner --suite tool_selection --judge --judge-model local

    # Run every dataset
    python -m evals.runner --all

Writes evals/runs/run_<ts>.json incrementally as cases finish (so a killed run still
keeps completed results) + evals/runs/latest.md (agent reads this).
Exit code 0 = all passed, 1 = failures (CI-friendly).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Windows consoles default to cp1252 — force UTF-8 so report glyphs (Δ, …) don't crash printing.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Project root on sys.path so `src...` imports resolve when run as a module or script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.core import (Case, CaseResult, load_suite, list_suites,
                        check_assertions, check_live_assertions, render_report,
                        persist_transcripts, aggregate_runs, RUNS_DIR)
from evals.triage import cluster_failures, render_triage


# Bound a stuck judge so a suite cannot hang forever — but keep this >= the per-rubric
# timeout in judges.py (full SUMMARY+trajectory prompts routinely need several minutes).
JUDGE_CALL_TIMEOUT = int(os.getenv("EVAL_JUDGE_CALL_TIMEOUT_S", "1200"))


async def _run_judges(case: Case, artifacts: dict, judge_model: str):
    """Run each declared rubric, passing whatever lane-agnostic artifacts are available."""
    from evals import judges
    adapter = build_judge_adapter_cached(judge_model)
    out = []
    for rubric in case.judge:
        fn = judges.JUDGES.get(rubric)
        if not fn:
            out.append({"rubric": rubric, "passed": True, "score": None,
                        "reasons": [f"unknown rubric '{rubric}'"]})
            continue
        try:
            out.append(await asyncio.wait_for(fn(adapter, prompt=case.prompt, **artifacts),
                                              timeout=JUDGE_CALL_TIMEOUT))
        except asyncio.TimeoutError:
            out.append({
                "rubric": rubric, "passed": False, "score": None, "needs_rejudge": True,
                "diag": {"kind": "timeout", "likely_cause": "judge_timeout",
                         "user_chars": None, "est_input_tokens": None, "num_ctx": None},
                "reasons": [
                    f"needs_rejudge: judge call exceeded {JUDGE_CALL_TIMEOUT}s",
                    "diag: likely_cause=judge_timeout",
                ],
            })
        except Exception as e:
            out.append({
                "rubric": rubric, "passed": False, "score": None, "needs_rejudge": True,
                "diag": {"kind": "error", "likely_cause": "judge_error",
                         "error_type": type(e).__name__},
                "reasons": [
                    f"needs_rejudge: judge error: {type(e).__name__}: {e}",
                    f"diag: likely_cause=judge_error error_type={type(e).__name__}",
                ],
            })
    return out


_ADAPTER_CACHE = {}


def build_judge_adapter_cached(mode: str):
    if mode not in _ADAPTER_CACHE:
        from evals.judges import build_judge_adapter
        _ADAPTER_CACHE[mode] = build_judge_adapter(mode)
    return _ADAPTER_CACHE[mode]


def _finalize(case, result: CaseResult) -> CaseResult:
    """Attach the non-gating diagnostics every lane should record.

    Kept in one place so a new lane can't accidentally ship without budget tracking or
    tool-selection metrics — the two things that make a failure explainable after the fact.
    """
    from evals.outcomes import budget_is_hard, check_budgets, tool_selection_metrics

    breaches = check_budgets(case.budget, result.metrics)
    result.budget_warnings = breaches
    # A case may opt into its budgets gating, but that has to be an explicit per-case
    # decision recorded in the dataset — never the default.
    if breaches and budget_is_hard(case.budget):
        result.assertion_failures = list(result.assertion_failures) + [
            f"budget (hard): {b}" for b in breaches]
        result.passed = False

    expected_tools = list(case.expect.get("tools_include", []) or [])
    # Only score precision/recall when the case actually DECLARED an expectation. Scoring
    # against an empty expected set produces a meaningless "tool_precision: 0.0" that reads
    # like "called N wrong tools" when in fact no assertion was ever made — caught live on
    # an undeclared-expectation probe (see memory: don't-fabricate-personal-data-in-eval-prompts)
    # where every one of 12 real, correct tool calls showed up as an "extra"/wrong tool.
    if expected_tools:
        actual_tools = result.metrics.get("tool_names") or []
        result.metrics.update(tool_selection_metrics(expected_tools, actual_tools))

    # Resolve the outcome now, while the lane's metrics are fresh, so `void_reason` is
    # populated for the report and the persisted record.
    result.classify()
    return result


# Dry-run tool-selection is cheap (9B already resident) — default to the measured
# concurrency ceiling from evals/parallel_probe.py (~6 on this box). Override with
# --concurrency N. Live/workflow ignore this.
DEFAULT_DRY_CONCURRENCY = int(os.getenv("EVAL_DRY_CONCURRENCY", "6"))


class _ProgressPrinter:
    """Live, easy-to-scan progress: done/total, pass/fail, elapsed, ETA."""

    def __init__(self, total: int):
        self.total = max(1, total)
        self.done = 0
        self.passed = 0
        self.failed = 0
        self.t0 = time.perf_counter()

    def record(self, case_id: str, ok: bool, detail: str = "") -> None:
        self.done += 1
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        elapsed = time.perf_counter() - self.t0
        remaining = self.total - self.done
        per = elapsed / self.done
        eta = per * remaining
        verdict = "PASS" if ok else "FAIL"
        extra = f"  {detail}" if detail else ""
        print(
            f"  [{self.done}/{self.total}] {case_id}: {verdict}{extra}\n"
            f"           {self.passed} pass · {self.failed} fail · "
            f"{elapsed:.0f}s elapsed · {remaining} left"
            + (f" (~{eta:.0f}s)" if remaining else ""),
            flush=True,
        )


async def _run_dry(suite: str, cases, do_judge, judge_model, quiet, repeat=1,
                   concurrency=DEFAULT_DRY_CONCURRENCY, on_progress=None):
    from evals.dry_run import DryRunHarness

    async def _one(harness, case) -> CaseResult:
        payload = await harness.capture(case.prompt, category=case.category)
        if payload.error:
            return CaseResult(case.id, case.prompt, passed=False, error=payload.error, lane="dry-run")
        fails = check_assertions(case.expect, payload)
        metrics = {"tool_count": payload.tool_count, "tool_names": payload.tool_names,
                   "system_tokens": payload.system_tokens, "total_tokens": payload.total_tokens,
                   "prompts_offline": payload.prompts_offline}
        result = CaseResult(case.id, case.prompt, passed=not fails,
                            assertion_failures=fails, metrics=metrics, lane="dry-run")
        result.transcript = {"system_prompt": payload.system_prompt, "tool_names": payload.tool_names,
                             "system_tokens": payload.system_tokens, "total_tokens": payload.total_tokens}
        if do_judge and case.judge:
            artifacts = {"tool_names": payload.tool_names, "system_prompt": payload.system_prompt}
            result.judge_results = await _run_judges(case, artifacts, judge_model)
        return _finalize(case, result)

    n_workers = max(1, min(concurrency, len(cases)))
    results: list = [None] * len(cases)
    progress = _ProgressPrinter(len(cases))

    def _note() -> None:
        if on_progress:
            on_progress(results)

    if n_workers == 1:
        harness = await DryRunHarness(quiet=quiet).setup()
        for i, case in enumerate(cases):
            runs = [await _one(harness, case) for _ in range(repeat)]
            results[i] = aggregate_runs(case, runs)
            _note()
            rp = f" [{results[i].repeat['n_passed']}/{repeat}]" if results[i].repeat else ""
            m = results[i].metrics
            progress.record(
                case.id, results[i].overall_passed,
                detail=f"{rp} ({m.get('tool_count')} tools, {m.get('system_tokens')} sys tok)".strip(),
            )
        return results

    # Parallel lane: one DryRunHarness PER WORKER (not shared) — the harness's spy
    # object is mutated in place on every capture() call (`self._spy.captured_messages =
    # []`), so sharing one harness across concurrent calls races and cross-contaminates
    # results between cases. Each worker owns its harness exclusively and drains the
    # shared queue sequentially, so within a worker there's no race, and across workers
    # there's no shared mutable state at all.
    print(f"[evals] dry-run concurrency={n_workers} (each worker owns its own harness)", flush=True)
    harnesses = await asyncio.gather(*(DryRunHarness(quiet=quiet).setup() for _ in range(n_workers)))
    queue: asyncio.Queue = asyncio.Queue()
    for i, case in enumerate(cases):
        queue.put_nowait((i, case))
    lock = asyncio.Lock()

    async def _worker(harness):
        while True:
            try:
                i, case = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            runs = [await _one(harness, case) for _ in range(repeat)]
            results[i] = aggregate_runs(case, runs)
            async with lock:
                _note()
                rp = f" [{results[i].repeat['n_passed']}/{repeat}]" if results[i].repeat else ""
                m = results[i].metrics
                progress.record(
                    case.id, results[i].overall_passed,
                    detail=f"{rp} ({m.get('tool_count')} tools, {m.get('system_tokens')} sys tok)".strip(),
                )

    await asyncio.gather(*(_worker(h) for h in harnesses))
    return results


async def _run_live(suite: str, cases, do_judge, judge_model, repeat=1, on_progress=None):
    from evals.live import probe_live, server_is_up, load_auth_token, open_eval_ws
    if not server_is_up():
        print("[evals] ERROR: --live needs a running Brain server on :8767.\n"
              "  Start one (free local model): $env:PYTHONPATH='.'; $env:CHAD_DAEMONS_ENABLED='false'; "
              ".venv\\Scripts\\python.exe src\\remote\\server.py")
        return [CaseResult(c.id, c.prompt, passed=False, error="server not running", lane="live")
                for c in cases]
    token = load_auth_token()

    async def _one(case, ws) -> CaseResult:
        trace = await probe_live(case.prompt, category=case.category, auth_token=token, ws=ws)
        if trace.error:
            return CaseResult(case.id, case.prompt, passed=False, error=trace.error,
                              metrics={"duration_s": trace.duration_s}, lane="live")
        fails = check_live_assertions(case.expect, trace)
        metrics = {"tool_count": trace.tool_count, "tool_names": trace.tool_names,
                   "plan_steps": len(trace.plan) if trace.plan else 0,
                   "response_chars": len(trace.final_response or ""),
                   "reasoning_chars": len(trace.reasoning_full), "duration_s": trace.duration_s,
                   "total_tokens": trace.total_tokens}
        result = CaseResult(case.id, case.prompt, passed=not fails,
                            assertion_failures=fails, metrics=metrics, lane="live")
        result.transcript = {"tool_names": trace.tool_names, "tool_calls": trace.tool_calls,
                             "plan": trace.plan,
                             "response": trace.final_response, "tool_results": trace.tool_results,
                             "reasoning": trace.reasoning_full,
                             "events": list(trace.events),
                             "stop_reason": trace.stop_reason,
                             "gather_brief": trace.gather_brief,
                             "goal": trace.goal,
                             "reasoning_by_agent": {a: t for a, t in trace.reasoning_by_agent.items()}}
        if do_judge and case.judge:
            artifacts = {"tool_names": trace.tool_names, "plan": trace.plan,
                         "response": trace.final_response or "", "tool_results": trace.tool_results,
                         "stop_reason": trace.stop_reason, "live": True,
                         "events": list(trace.events),
                         "gather_brief": trace.gather_brief}
            result.judge_results = await _run_judges(case, artifacts, judge_model)
        return _finalize(case, result)

    results = []
    progress = _ProgressPrinter(len(cases))
    try:
        async with open_eval_ws(token) as ws:
            print("[evals] live: reusing one WS connection for the suite", flush=True)
            for case in cases:
                runs = [await _one(case, ws) for _ in range(repeat)]
                result = aggregate_runs(case, runs)
                results.append(result)
                if on_progress:
                    on_progress(results)
                rp = f" [{result.repeat['n_passed']}/{repeat}]" if result.repeat else ""
                m = result.metrics
                progress.record(
                    case.id, result.overall_passed,
                    detail=(f"{rp} ({m.get('tool_count')} tools, {m.get('plan_steps')} plan steps, "
                            f"{m.get('response_chars')} resp chars, {m.get('duration_s')}s)").strip(),
                )
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        if not results:
            return [CaseResult(c.id, c.prompt, passed=False, error=err, lane="live")
                    for c in cases]
        done_ids = {r.case_id for r in results}
        for c in cases:
            if c.id not in done_ids:
                results.append(CaseResult(c.id, c.prompt, passed=False, error=err, lane="live"))
    return results


async def _run_workflow(suite: str, cases, do_judge, judge_model, approval, max_rounds, repeat=1,
                        on_progress=None):
    """Drive each case as a FULL workflow: plan → approve → execute until done/failed."""
    from evals.live import probe_workflow, server_is_up, load_auth_token, open_eval_ws
    if not server_is_up():
        print("[evals] ERROR: --workflow needs a running Brain server on :8767.\n"
              "  Start one (free local model): $env:PYTHONPATH='.'; $env:CHAD_DAEMONS_ENABLED='false'; "
              ".venv\\Scripts\\python.exe src\\remote\\server.py")
        return [CaseResult(c.id, c.prompt, passed=False, error="server not running", lane="workflow")
                for c in cases]
    token = load_auth_token()

    async def _one(case, ws) -> CaseResult:
        # Honor the case category. Older code remapped category="agent" → "planning" so every
        # --workflow case entered the plan lane; that made agent-lane HITL site builds re-enter
        # scout/planner (dinner batch 2026-07-24) and thrash on awaiting_approval. Workflow
        # datasets that want the plan lane must set category="planning" explicitly (they already do).
        category = case.category or "planning"
        trace = await probe_workflow(case.prompt, category=category, approval=approval,
                                     max_rounds=max_rounds, auth_token=token, ws=ws)
        if trace.error and not trace.final_response:
            return CaseResult(case.id, case.prompt, passed=False, error=trace.error,
                              metrics={"rounds": trace.rounds, "duration_s": trace.duration_s},
                              lane="workflow")
        fails = check_live_assertions(case.expect, trace)
        metrics = {"tool_count": trace.tool_count, "tool_names": trace.tool_names,
                   "plan_steps": len(trace.plan) if trace.plan else 0,
                   "steps_completed": trace.steps_completed, "steps_total": trace.steps_total,
                   "rounds": trace.rounds, "completed": trace.completed,
                   "continue_nudges": trace.continue_nudges,
                   "stop_reason": trace.stop_reason,
                   "response_chars": len(trace.final_response or ""), "duration_s": trace.duration_s,
                   "total_tokens": trace.total_tokens}
        result = CaseResult(case.id, case.prompt, passed=not fails,
                            assertion_failures=fails, metrics=metrics, error=trace.error or None,
                            lane="workflow")
        # Pull the FULL nested execution trace from LangSmith (every subagent turn + all their
        # internal tool/search calls) — the WS stream only carries top-level turn-completes, so
        # without this the transcript is blind to the deep-research work. Best-effort: never let a
        # tracing hiccup fail the case.
        langsmith_md = ""
        if trace.session_id:
            try:
                from evals.langsmith_trace import fetch_session_trace, render_trace_markdown
                langsmith_md = render_trace_markdown(fetch_session_trace(trace.session_id))
            except Exception as e:  # noqa: BLE001
                langsmith_md = f"## LangSmith trace\n_fetch failed: {e}_\n"
        result.transcript = {"tool_names": trace.tool_names, "tool_calls": trace.tool_calls,
                             "plan": trace.plan, "final_plan": trace.final_plan, "goal": trace.goal,
                             "rounds_detail": trace.rounds_detail,
                             "response": trace.final_response, "tool_results": trace.tool_results,
                             "reasoning": trace.reasoning_full, "rounds": trace.rounds,
                             "completed": trace.completed, "stop_reason": trace.stop_reason,
                             "session_id": trace.session_id, "langsmith": langsmith_md,
                             "events": list(trace.events),
                             "reasoning_by_agent": {a: t for a, t in trace.reasoning_by_agent.items()}}
        if do_judge and case.judge:
            artifacts = {"tool_names": trace.tool_names, "plan": trace.plan,
                         "response": trace.final_response or "", "tool_results": trace.tool_results,
                         "completed": trace.completed, "stop_reason": trace.stop_reason,
                         "live": True, "events": list(trace.events),
                         "gather_brief": trace.gather_brief}
            result.judge_results = await _run_judges(case, artifacts, judge_model)
        return _finalize(case, result)

    results = []
    progress = _ProgressPrinter(len(cases))
    try:
        async with open_eval_ws(token) as ws:
            print("[evals] workflow: reusing one WS connection for the suite", flush=True)
            for case in cases:
                runs = [await _one(case, ws) for _ in range(repeat)]
                result = aggregate_runs(case, runs)
                results.append(result)
                if on_progress:
                    on_progress(results)
                rp = f" [{result.repeat['n_passed']}/{repeat}]" if result.repeat else ""
                m = result.metrics
                progress.record(
                    case.id, result.overall_passed,
                    detail=(f"{rp} ({m.get('rounds')} rounds, completed={m.get('completed')}, "
                            f"steps {m.get('steps_completed')}/{m.get('steps_total')}, "
                            f"{m.get('tool_count')} tools, {m.get('plan_steps')} plan steps, "
                            f"nudges={m.get('continue_nudges', 0)}, {m.get('duration_s')}s)").strip(),
                )
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        if not results:
            return [CaseResult(c.id, c.prompt, passed=False, error=err, lane="workflow")
                    for c in cases]
        done_ids = {r.case_id for r in results}
        for c in cases:
            if c.id not in done_ids:
                results.append(CaseResult(c.id, c.prompt, passed=False, error=err, lane="workflow"))
    return results


async def run_suite(suite: str, do_judge: bool, judge_model: str, quiet: bool,
                    live: bool = False, workflow: bool = False, limit: int = 0,
                    approval: str = None, max_rounds: int = 6, repeat: int = 1,
                    concurrency: int = DEFAULT_DRY_CONCURRENCY, on_progress=None):
    from evals.live import WORKFLOW_APPROVAL_ANSWER
    approval = approval or WORKFLOW_APPROVAL_ANSWER
    cases = load_suite(suite)
    if limit:
        cases = cases[:limit]
    lane = "workflow" if workflow else ("live" if live else "dry-run")
    rpt = f" | repeat={repeat}× (pass@k/pass^k)" if repeat > 1 else ""
    conc = f" | concurrency={concurrency}" if (not live and not workflow and concurrency > 1) else ""
    print(f"[evals] {suite}: {len(cases)} cases | lane={lane}"
          f"{' + judge(' + judge_model + ')' if do_judge else ' (free)'}{rpt}{conc}")
    if workflow:
        results = await _run_workflow(suite, cases, do_judge, judge_model, approval, max_rounds,
                                      repeat, on_progress=on_progress)
    elif live:
        results = await _run_live(suite, cases, do_judge, judge_model, repeat,
                                  on_progress=on_progress)
    else:
        results = await _run_dry(suite, cases, do_judge, judge_model, quiet, repeat, concurrency,
                                 on_progress=on_progress)
    return cases, results


def _case_payload(r: CaseResult) -> dict:
    return {
        "id": r.case_id, "outcome": r.outcome, "passed": r.outcome == "pass",
        "void_reason": r.void_reason,
        "assertion_failures": r.assertion_failures,
        "budget_warnings": r.budget_warnings,
        "judge_results": r.judge_results, "metrics": r.metrics, "error": r.error,
        "repeat": r.repeat,
    }


def _write_run_json(suite: str, results, do_judge: bool, lane: str, manifest: dict,
                    ts: str, *, partial: bool, planned: int) -> Path:
    """Flush completed CaseResults to run_<ts>.json. Safe to call mid-run (#632)."""
    from evals.outcomes import summarize

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    completed = [r for r in results if r is not None]
    summary = summarize(completed)
    payload = {
        "suite": suite, "timestamp": ts, "judged": do_judge, "lane": lane,
        "partial": partial,
        "completed": len(completed),
        "planned": planned,
        "summary": summary,
        "manifest": manifest or {},
        "cases": [_case_payload(r) for r in completed],
    }
    path = RUNS_DIR / f"run_{ts}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = render_report(suite, completed, do_judge, lane=lane)
    if partial:
        report = (f"_partial run: {len(completed)}/{planned} cases completed — "
                  f"killed/crashed mid-run keeps this snapshot_\n\n" + report)
    (RUNS_DIR / "latest.md").write_text(report, encoding="utf-8")
    return path


def _persist(suite: str, cases, results, do_judge: bool, lane: str = "dry-run",
             manifest: dict = None, ts: str = None):
    from evals.outcomes import summarize
    from evals.scoreboard import append_history

    ts = ts or time.strftime("%Y%m%d_%H%M%S")
    # Keep case↔result alignment for transcripts even if a slot was somehow skipped.
    paired = [(c, r) for c, r in zip(cases, results) if r is not None]
    paired_cases = [c for c, _ in paired]
    completed = [r for _, r in paired]
    _write_run_json(suite, completed, do_judge, lane, manifest or {}, ts,
                    partial=False, planned=len(cases))
    summary = summarize(completed)

    # Full per-case trajectories for review + root-cause triage of failures.
    tdir = persist_transcripts(ts, paired_cases, completed)
    clusters = cluster_failures(completed)
    (RUNS_DIR / "triage.md").write_text(render_triage(suite, completed, clusters), encoding="utf-8")

    # Append the durable trend point (tracked in git, unlike the transient run_*.json).
    # Only on finalize — partial mid-run snapshots must not pollute the scoreboard.
    append_history({
        "suite": suite, "timestamp": ts, "judged": do_judge, "lane": lane,
        "summary": summary, "manifest": manifest or {},
        "cases": [_case_payload(r) for r in completed],
    })

    report = (RUNS_DIR / "latest.md").read_text(encoding="utf-8")
    print("\n" + report)
    print(f"\n[evals] saved: evals/runs/run_{ts}.json  |  report: latest.md  |  "
          f"triage: triage.md  |  transcripts: {tdir.relative_to(RUNS_DIR.parent)}/")
    if clusters:
        print("[evals] top failure clusters: " +
              "; ".join(f"{c.label} ({len(c.cases)})" for c in clusters[:3]))
    if summary["void"]:
        print(f"[evals] WARNING: {summary['void']} case(s) VOIDED (excluded from the "
              f"{summary['pass_rate']} pass rate): {summary['void_by_reason']}")
        if not summary["trustworthy"]:
            print("[evals] this run's pass rate is NOT comparable to a clean run — "
                  ">10% of cases never reached a healthy environment. Fix the infra "
                  "(python -m evals.preflight) and re-run.")

    # Exit-code contract: a run that could not fairly test the model is not a "pass",
    # but it is also not a model failure — the caller distinguishes via `trustworthy`.
    return summary["failed"] == 0 and summary["trustworthy"]


def main():
    # This offline lane must not start the model gate or import application code.
    if "--project-context" in sys.argv:
        from evals.project_context_probe import main as context_main
        raise SystemExit(context_main([arg for arg in sys.argv[1:] if arg != "--project-context"]))
    # Bring up lmstudio-gate (cross-process LM Studio priority queue, issue #478) so this
    # runner's judge calls funnel through the same priority queue as the real Brain turns
    # they share the LM Studio slot with, instead of contending. Best-effort / idempotent:
    # if the Brain already started it we just reuse it; if it can't start, judges go direct.
    try:
        from src.infrastructure.llm.gate_client import ensure_gate_running
        ensure_gate_running()
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Chad harness eval runner")
    ap.add_argument("--suite", "-s", help="dataset name (e.g. tool_selection) or path")
    ap.add_argument("--all", action="store_true", help="run every dataset in evals/datasets/")
    ap.add_argument("--judge", action="store_true", help="enable LLM-as-judge rubrics")
    ap.add_argument("--judge-model", default="qwen/qwen3.5-9b",
                    help="judge model (default: qwen/qwen3.5-9b — a small loaded LM Studio model). "
                         "Pass any LM Studio id (e.g. 'google/gemma-4-31b-qat'), or 'local'/'cloud' to "
                         "follow config. NEVER name a heavy model (qwen/qwen3.6-35b-a3b) — it stalls VRAM.")
    ap.add_argument("--live", action="store_true",
                    help="run each case through the REAL running server (Phase 1); "
                         "enables planner_quality / response_grounded + executed-tool assertions")
    ap.add_argument("--workflow", action="store_true",
                    help="drive the FULL workflow per case (plan → auto-approve → execute until "
                         "done/failed); enables must_complete/max_rounds + the workflow_outcome judge")
    ap.add_argument("--approval", default=None,
                    help="text sent to resume a paused plan in --workflow mode "
                         "(default: Approved — execute the existing plan…; avoid bare 'proceed')")
    ap.add_argument("--max-rounds", type=int, default=30, metavar="N",
                    help="max plan/approve/execute turns before a workflow is declared stuck (default: 30)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="run only the first N cases (cheap smoke / PoC)")
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="run each case N times for pass@k/pass^k + variance (agent output is "
                         "non-deterministic; 1 run is noisy). Case gates on pass^k (all N passed). "
                         "Flaky cases (some-but-not-all) are flagged. Default: 1.")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_DRY_CONCURRENCY, metavar="N",
                    help="dry-run lane only: run N cases in parallel against the local 9B tool "
                         "selector, each with its own isolated harness (no shared state races). "
                         f"Default: {DEFAULT_DRY_CONCURRENCY} (EVAL_DRY_CONCURRENCY / measured "
                         "ceiling from parallel_probe). Pass 1 for sequential. "
                         "Ignored by --live/--workflow.")
    ap.add_argument("--models", metavar="A,B,C",
                    help="head-to-head model comparison (requires --live or --workflow). "
                         "Hot-swaps between already-loaded LM Studio models only — never JIT-loads. "
                         "Example: --models \"google/gemma-4-31b-qat,qwen/qwen3-14b\"")
    ap.add_argument("--replay", action="store_true",
                    help="OATS replay: offline K-fold holdout measuring whether embedding "
                         "refinement improves tool SELECTION (rank/recall, base vs refined). "
                         "Free — no server, no model tokens. Reads evals/runs/replay_latest.md")
    ap.add_argument("--prompt-audit", action="store_true",
                    help="audit the PROMPT ASSET itself: assemble every scope's real system "
                         "prompt and flag placeholder leaks, duplicate/near-duplicate fragments, "
                         "and bloat (free); add --judge for the contradiction rubric per scope")
    ap.add_argument("--tool-contract", action="store_true",
                    help="native-tool contract gate: every native tool needs a display "
                         "formatter AND a tool-ui.tsx entry. Ratchets against "
                         "evals/datasets/tool_contract_baseline.json — fails only on NEW "
                         "drift. Free — no server, no model tokens.")
    ap.add_argument("--persona-parity", action="store_true",
                    help="persona-parity gate (#589): every fragment in the personal overlay "
                         "(config/persona) must have a generic counterpart in the committed "
                         "baseline (prompts/persona), so a fresh clone never boots missing a "
                         "prompt. Also validates scopes and flags deprecated ids. Content is "
                         "ALLOWED to diverge. Free — no server, no model tokens.")
    ap.add_argument("--memory-layer", action="store_true",
                    help="memory-layer fixture probes (#618): seed Drive catalog / filesystem / "
                         "ActivityRAG fixtures into a temp sqlite DB and assert must_include / "
                         "must_not on retrieval (no chat LLM). Free.")
    ap.add_argument("--graph-memory", action="store_true",
                    help="typed knowledge-graph fixture probes (#610, MVP A3 acceptance): seed a "
                         "real GraphRAGClient from a fixture `_graph` block and assert must_include "
                         "/ must_not on relationship-walk retrieval (no chat LLM). Free.")
    ap.add_argument("--memory-write-live", action="store_true",
                    help="LIVE memory-write behaviour (#660): runs a REAL turn per case and "
                         "checks whether the model SAVES the right things — durable facts saved, "
                         "chatter/tasks not, corrections superseded, Tier-1 topics offered rather "
                         "than silently stored. Also reports repair@2 for gate rejections. "
                         "COSTS MODEL TOKENS — not part of the free CI set.")
    ap.add_argument("--judge-calibration", action="store_true",
                    help="judge-vs-human agreement (issue #312): re-run every hand-labeled case "
                         "in evals/datasets/judge_golden.jsonl through --judge-model and report "
                         "agreement %% overall + per-rubric, with every disagreement shown. "
                         "One judge call per golden case — same cost as --judge on a normal suite. "
                         "Reads evals/runs/judge_calibration_latest.md")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="skip the environment gate. Only for debugging the harness itself — "
                         "a run against a broken environment produces results that look like "
                         "model failures and cannot be told apart afterwards.")
    ap.add_argument("--preflight", action="store_true",
                    help="run ONLY the environment gate for the selected suites/lane and exit "
                         "(exit 0 = clear, 2 = blocked). Run this before any long sweep.")
    ap.add_argument("--scoreboard", action="store_true",
                    help="print the longitudinal scoreboard (pass-rate trend, regressed, "
                         "flaky, saturated, never-passed cases) and exit")
    ap.add_argument("--coverage", action="store_true",
                    help="print the eval coverage report (which registered tools have no "
                         "case, unknown tool names, suites lacking negative cases) and exit")
    ap.add_argument("--list", action="store_true", help="list available suites and exit")
    ap.add_argument("--verbose", action="store_true", help="show Supabase/warmup chatter")
    args = ap.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if args.list:
        from evals.core import list_probe_datasets
        print("Available suites:", ", ".join(list_suites()) or "(none)")
        probes = list_probe_datasets()
        if probes:
            # Surfaced rather than hidden: these live in datasets/ but use their own schema
            # and are read by standalone probes, so the runner cannot execute them.
            print("Probe datasets (own schema, not runner-executable):", ", ".join(probes))
        return

    if args.scoreboard:
        from evals.scoreboard import render_scoreboard
        out = render_scoreboard(suite=args.suite, lane=("workflow" if args.workflow
                                                        else "live" if args.live else None))
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "scoreboard.md").write_text(out, encoding="utf-8")
        print(out)
        return

    if args.coverage:
        from evals.scoreboard import render_coverage
        out = render_coverage()
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "coverage.md").write_text(out, encoding="utf-8")
        print(out)
        return

    if args.preflight:
        from evals.preflight import main as preflight_main
        argv = []
        for s in (list_suites() if args.all else ([args.suite] if args.suite else [])):
            argv += ["--suite", s]
        if args.live:
            argv.append("--live")
        if args.workflow:
            argv.append("--workflow")
        if args.judge:
            argv += ["--judge-model", args.judge_model]
        argv += ["--repeat", str(max(1, args.repeat))]
        sys.exit(preflight_main(argv))

    if args.memory_layer:
        from evals.memory_layer_probe import run_memory_layer_probe, render_report
        print("[evals] memory-layer | lane=memory-layer (free, fixture retrieval, no LLM)")
        results = asyncio.run(run_memory_layer_probe())
        report = render_report(results)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "memory_layer_latest.md").write_text(report, encoding="utf-8")
        print("\n" + report)
        sys.exit(0 if all(r.passed for r in results) else 1)

    if args.graph_memory:
        from evals.graph_memory_probe import run_graph_memory_probe, render_report
        print("[evals] graph-memory | lane=graph-memory (free, fixture retrieval, no LLM)")
        results = asyncio.run(run_graph_memory_probe())
        report = render_report(results)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "graph_memory_latest.md").write_text(report, encoding="utf-8")
        print("\n" + report)
        sys.exit(0 if all(r.passed for r in results) else 1)

    if args.memory_write_live:
        from evals.memory_write_live import run as run_memory_write_live
        from evals.memory_write_live import render_report as render_write_live
        print("[evals] memory-write-live | lane=memory-write-live "
              "(LIVE — real turns, COSTS MODEL TOKENS)")
        results = asyncio.run(run_memory_write_live(limit=args.limit))
        report = render_write_live(results)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "memory_write_live_latest.md").write_text(report, encoding="utf-8")
        print("\n" + report)
        sys.exit(0 if all(r.passed or r.void_reason for r in results) else 1)

    if args.replay:
        from evals.replay_lane import run_replay, render_report
        print("[evals] OATS replay | lane=replay (free, offline holdout)")
        m, s = run_replay()
        report = render_report(m, s)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "replay_latest.md").write_text(report, encoding="utf-8")
        print("\n" + report)
        base_r, ref_r = s["success_mean_rank_base"], s["success_mean_rank_refined"]
        regressed = (base_r is not None and ref_r is not None and (ref_r - base_r) > 0.05)
        sys.exit(1 if regressed else 0)

    if args.tool_contract:
        from evals.tool_contract import run_tool_contract
        print("[evals] tool-contract | lane=tool-contract (free, deterministic)")
        passed, report = run_tool_contract()
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "tool_contract_latest.md").write_text(report, encoding="utf-8")
        print("\n" + report)
        sys.exit(0 if passed else 1)

    if args.persona_parity:
        from evals.persona_parity import run_persona_parity
        print("[evals] persona-parity | lane=persona-parity (free, deterministic)")
        passed, report = run_persona_parity()
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "persona_parity_latest.md").write_text(report, encoding="utf-8")
        print("\n" + report)
        sys.exit(0 if passed else 1)

    if args.judge_calibration:
        from evals.judge_calibration import run_calibration, render_report
        print(f"[evals] judge-calibration | lane=judge-calibration | judge={args.judge_model}")
        results = asyncio.run(run_calibration(args.judge_model))
        report = render_report(args.judge_model, results)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / "judge_calibration_latest.md").write_text(report, encoding="utf-8")
        print("\n" + report)
        # Diagnostic/measurement lane, not a regression gate — "agreement is only 70%" is a
        # valid, useful finding to record (issue #312), not a build failure. Only fail the
        # exit code if the run produced nothing usable at all (e.g. no judge reachable).
        scored = [r for r in results if r.agrees is not None]
        sys.exit(1 if not scored else 0)

    if args.prompt_audit:
        from evals.prompt_audit import run_prompt_audit
        print(f"[evals] prompt-audit | lane=prompt-audit"
              f"{' + judge(' + args.judge_model + ')' if args.judge else ' (free)'}")
        cases, results = asyncio.run(
            run_prompt_audit(args.judge, args.judge_model, quiet=not args.verbose))
        ok = _persist("prompt_audit", cases, results, args.judge, lane="prompt-audit")
        sys.exit(0 if ok else 1)

    suites = list_suites() if args.all else ([args.suite] if args.suite else None)
    if not suites:
        ap.error("provide --suite NAME, --all, or --list")

    lane = "workflow" if args.workflow else ("live" if args.live else "dry-run")

    # ── Preflight gate ────────────────────────────────────────────────────────
    # Prove the environment can fairly run these cases BEFORE spending hours on them.
    # This is not a nicety: a dead Google token (#476) or an empty search backend silently
    # converts an overnight sweep into hours of fake "model failed" results.
    manifest: dict = {}
    if not args.skip_preflight:
        from evals.preflight import (PreflightContext, estimate_run_seconds,
                                     requirements_for_cases, run_preflight)
        from evals.scoreboard import build_manifest

        pf_cases, load_errors = [], {}
        for suite in suites:
            try:
                pf_cases.extend(load_suite(suite))
            except Exception as e:
                load_errors[suite] = f"{type(e).__name__}: {e}"
        if args.limit:
            pf_cases = pf_cases[:args.limit]

        reqs = requirements_for_cases(pf_cases, lanes=[lane])
        for case in pf_cases:
            reqs.update(getattr(case, "requires", []) or [])

        ctx = PreflightContext(
            judge_model=args.judge_model if args.judge else None,
            lanes=[lane], suites=list(suites), cases=pf_cases, load_errors=load_errors,
            estimated_run_s=estimate_run_seconds(
                len(pf_cases), [lane], max(1, args.repeat),
                concurrency=max(1, args.concurrency) if lane == "dry-run" else 1),
        )
        report = run_preflight(ctx, reqs)
        print(report.render())
        manifest = build_manifest(
            suites=suites, lane=lane, judge_model=args.judge_model if args.judge else None,
            judged=args.judge, repeat=max(1, args.repeat), preflight=report.manifest())
        if not report.ok:
            print("[evals] ABORTED by preflight - nothing was run, so no misleading results "
                  "were produced. Fix the blocking checks above, or re-run with "
                  "--skip-preflight if you accept that the results may be uninterpretable.")
            sys.exit(2)
    else:
        from evals.scoreboard import build_manifest
        manifest = build_manifest(
            suites=suites, lane=lane, judge_model=args.judge_model if args.judge else None,
            judged=args.judge, repeat=max(1, args.repeat),
            preflight={"skipped": True})
        print("[evals] preflight SKIPPED (--skip-preflight) - results may be uninterpretable "
              "if an external dependency is down.")

    all_passed = True
    for suite in suites:
        if args.models:
            from evals.compare import persist_comparison, run_model_comparison
            model_ids = [m.strip() for m in args.models.split(",") if m.strip()]
            if len(model_ids) < 1:
                ap.error("--models needs at least one model id")
            runs, ok = asyncio.run(run_model_comparison(
                suite=suite, model_ids=model_ids,
                do_judge=args.judge, judge_model=args.judge_model,
                quiet=not args.verbose, live=args.live, workflow=args.workflow,
                limit=args.limit, approval=args.approval,
                max_rounds=args.max_rounds, repeat=max(1, args.repeat)))
            out = persist_comparison(suite, lane, runs, args.judge_model)
            report = out.read_text(encoding="utf-8")
            print("\n" + report)
            print(f"\n[compare] saved: {out.relative_to(PROJECT_ROOT)}  |  latest: evals/runs/compare_latest.md")
            all_passed = all_passed and ok
            continue

        # One timestamp for the whole suite so mid-run snapshots and the final
        # artifact share a path — a killed run keeps whatever cases already finished (#632).
        run_ts = time.strftime("%Y%m%d_%H%M%S")
        try:
            n_planned = len(load_suite(suite))
            if args.limit:
                n_planned = min(n_planned, args.limit)
        except Exception:
            n_planned = 0

        def on_progress(partial_results, _suite=suite, _ts=run_ts, _planned=n_planned):
            planned = _planned or len(partial_results)
            try:
                _write_run_json(_suite, partial_results, args.judge, lane, manifest, _ts,
                                partial=True, planned=planned)
            except Exception as e:  # noqa: BLE001 — never let snapshot I/O kill the run
                print(f"[evals] warning: incremental persist failed: {type(e).__name__}: {e}")

        cases, results = asyncio.run(
            run_suite(suite, args.judge, args.judge_model, quiet=not args.verbose,
                      live=args.live, workflow=args.workflow, limit=args.limit,
                      approval=args.approval, max_rounds=args.max_rounds, repeat=max(1, args.repeat),
                      concurrency=max(1, args.concurrency), on_progress=on_progress))
        ok = _persist(suite, cases, results, args.judge, lane=lane, manifest=manifest, ts=run_ts)
        all_passed = all_passed and ok

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
