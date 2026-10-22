"""
core.py — dataset model, deterministic assertions, and report rendering.

A *case* is one line of a .jsonl dataset:

  {"id": "tool_sel_email", "prompt": "check my emails for project updates",
   "category": "agent",
   "expect": {"tools_include": ["search_threads"], "tools_exclude": ["set_volume"],
              "max_system_tokens": 6000, "max_tools": 12},
   "judge": ["tool_appropriateness"]}

`expect` runs FREE (dry-run, no LLM). `judge` runs only with --judge.
Every key is optional — assert only what you care about.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# The "what does this result MEAN" layer lives in outcomes.py — one-way dependency.
# Only what core.py itself uses is imported here; budget/metric helpers are imported
# directly from evals.outcomes by their callers.
from evals.outcomes import (FAIL, PASS, VOID_HARNESS, VOID_OUTCOMES, _ADVISORY_EXPECT_KEYS,
                            _VALID_BUDGET_KEYS, classify_error, summarize)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
RUNS_DIR = Path(__file__).resolve().parent / "runs"

# Stop-reasons that mean "the graph paused for a human before doing anything irreversible"
# (plan approval or a clarifying question). Single source of truth — live.py imports this
# rather than redefining it, so the workflow lane's auto-resume logic and this module's
# requires_approval assertion can never silently drift apart.
PAUSE_REASONS = {"awaiting_approval", "awaiting_input"}

# Every key `check_assertions` (dry-run) or `check_live_assertions` (live/workflow) actually
# reads. Kept in sync manually — a typo'd expect key would otherwise be silently ignored
# (false green: the eval reports PASS while testing nothing).
_VALID_EXPECT_KEYS = {
    # dry-run lane
    "tools_include", "tools_exclude", "tools_any", "max_tools", "min_tools",
    "tools_nonempty", "min_tool_count", "max_system_tokens", "max_total_tokens",
    "system_includes", "system_excludes",
    # live lane
    "min_steps", "max_steps", "max_duration_s", "response_includes", "response_excludes",
    "min_response_chars", "tool_args", "max_tool_repeats",
    # At least one regex must match somewhere in the live tool_results blob (e.g. force
    # opening /inbox/conversations/<id> instead of answering from a list preview).
    "tool_results_regex_any",
    # workflow lane
    "must_complete", "max_rounds", "min_rounds", "min_steps_completed",
    # safety (live, single-turn)
    "requires_approval",
    # scout / plan-gather lane
    "scout_before_plan", "gather_brief_present", "gather_brief_min_chars",
    "scout_tools_include", "scout_tools_any", "scout_tools_exclude",
    "max_scout_pre_plan_tools", "plan_tools_include", "plan_tools_any", "plan_tools_exclude",
}



# ── Case + result models ─────────────────────────────────────────────────────


@dataclass
class Case:
    id: str
    prompt: str
    category: str = "agent"
    expect: Dict[str, Any] = field(default_factory=dict)
    judge: List[str] = field(default_factory=list)
    note: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    # Advisory shape/cost limits — measured and trended, non-gating unless {"hard": true}.
    budget: Dict[str, Any] = field(default_factory=dict)
    # Extra capabilities this case needs that can't be inferred from its expected tools
    # (e.g. a case whose prompt implies Drive but whose assertion only checks the response).
    # Consumed by evals.preflight.
    requires: List[str] = field(default_factory=list)
    suite: str = ""


@dataclass
class CaseResult:
    case_id: str
    prompt: str
    passed: bool
    assertion_failures: List[str] = field(default_factory=list)
    judge_results: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    lane: str = "dry-run"
    # Full trajectory for review — what the model saw and did. 2026 practice: "read
    # transcripts extensively." Populated per lane; rendered to a markdown file per case.
    transcript: Dict[str, Any] = field(default_factory=dict)
    # Multi-run aggregate (set by aggregate_runs when --repeat N > 1). Agent trajectories
    # are non-deterministic, so a single run is noisy. Holds {n_runs, n_passed, pass_at_k,
    # pass_hat_k, run_passes, metric_stats}. The visible fields above mirror a REPRESENTATIVE
    # run (a failing one if any), so report/triage/transcript keep working unchanged.
    repeat: Optional[Dict[str, Any]] = None
    # Advisory budget breaches: recorded and trended, never gating. Keeping these separate
    # from `assertion_failures` is what stops a slow-but-correct turn from reading as broken.
    budget_warnings: List[str] = field(default_factory=list)
    # Set by `classify()`. `void_reason` names the specific infrastructure/harness fault so
    # the report can cluster "8 cases voided: google_oauth_expired" instead of showing eight
    # unrelated-looking red rows.
    void_reason: str = ""

    @property
    def judge_passed(self) -> bool:
        # Intentional skips (score=None, no needs_rejudge) stay non-blocking.
        # needs_rejudge rubrics are NOT passes — see classify() → void_harness.
        return all(
            j.get("passed", True)
            for j in self.judge_results
            if not j.get("needs_rejudge")
        )

    @property
    def judge_needs_rejudge(self) -> bool:
        return any(j.get("needs_rejudge") for j in self.judge_results)

    @property
    def overall_passed(self) -> bool:
        # Multi-run: the case "passes" only if EVERY run passed (pass^k) — the conservative
        # regression gate. pass@k (≥1 success) is reported alongside but isn't the gate.
        if self.repeat:
            return bool(self.repeat.get("pass_hat_k"))
        if self.judge_needs_rejudge:
            return False
        return self.passed and self.judge_passed and not self.error

    def classify(self) -> str:
        """Resolve this result into the four-outcome taxonomy.

        Call once, after the lane has populated `error`/`metrics`. Idempotent.
        """
        if self.error:
            # "Did the turn actually do anything?" decides whether a timeout is the
            # environment's fault or the model's — see classify_error.
            produced_work = bool(
                self.metrics.get("tool_count")
                or self.metrics.get("steps_completed")
                or (self.metrics.get("response_chars") or 0) > 0
            )
            outcome, reason = classify_error(self.error, produced_work=produced_work)
            if outcome in VOID_OUTCOMES:
                self.void_reason = reason
                return outcome
            self.void_reason = reason      # e.g. turn_timeout_after_work — a real failure
            return FAIL
        # Judge empty/timeout/unparseable: model was never fairly scored — void for rejudge,
        # do not auto-PASS (HITL 2026-07-24 green-wash) and do not FAIL the Brain.
        if self.judge_needs_rejudge:
            self.void_reason = "judge_needs_rejudge"
            return VOID_HARNESS
        return PASS if self.overall_passed else FAIL

    @property
    def outcome(self) -> str:
        return self.classify()

    @property
    def is_void(self) -> bool:
        return self.outcome in VOID_OUTCOMES

    @property
    def counts_toward_pass_rate(self) -> bool:
        """VOID cases are excluded from the denominator. A run where half the cases never
        reached a healthy server has a pass rate over the half that did — anything else
        reports infrastructure downtime as a model regression."""
        return not self.is_void


# ── Dataset loading ──────────────────────────────────────────────────────────


def load_suite(name_or_path: str) -> List[Case]:
    """Accepts a bare suite name ('tool_selection'), a filename, or a path."""
    p = Path(name_or_path)
    candidates = [p, DATASETS_DIR / name_or_path,
                  DATASETS_DIR / f"{name_or_path}.jsonl"]
    path = next((c for c in candidates if c.exists()), None)
    if not path:
        raise FileNotFoundError(
            f"Suite '{name_or_path}' not found. Looked in: "
            f"{', '.join(str(c) for c in candidates)}")

    cases: List[Case] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name}:{i} invalid JSON: {e}")

        expect = d.get("expect", {})
        unknown_expect = set(expect) - _VALID_EXPECT_KEYS
        if unknown_expect:
            raise ValueError(
                f"{path.name}:{i} unknown expect key(s) {sorted(unknown_expect)} — "
                f"typo, or check_assertions/check_live_assertions doesn't read it "
                f"(this would silently no-op, not fail)")

        budget = d.get("budget", {})
        unknown_budget = set(budget) - _VALID_BUDGET_KEYS
        if unknown_budget:
            raise ValueError(
                f"{path.name}:{i} unknown budget key(s) {sorted(unknown_budget)} — "
                f"valid: {sorted(_VALID_BUDGET_KEYS)}")

        # Advisory keys left in `expect` are honoured but migrated into `budget`, so there
        # is exactly ONE place that decides whether a shape metric gates.
        for key in sorted(set(expect) & _ADVISORY_EXPECT_KEYS):
            budget.setdefault(key, expect.pop(key))

        judge = d.get("judge", [])
        from evals.judges import JUDGES as _JUDGES
        unknown_judge = [r for r in judge if r not in _JUDGES]
        if unknown_judge:
            raise ValueError(
                f"{path.name}:{i} unknown judge rubric(s) {unknown_judge} — "
                f"valid rubrics: {sorted(_JUDGES)}")

        if "prompt" not in d:
            raise ValueError(
                f"{path.name}:{i} has no 'prompt' — this file is not a runner suite. "
                f"Probe datasets with their own schema belong in evals/datasets/probes/ "
                f"(see list_probe_datasets)")

        cases.append(Case(
            id=d.get("id", f"case_{i}"),
            prompt=d["prompt"],
            category=d.get("category", "agent"),
            expect=expect,
            judge=judge,
            note=d.get("note", ""),
            raw=d,
            budget=budget,
            requires=list(d.get("requires", []) or []),
            suite=path.stem,
        ))
    return cases


_NUMERIC_METRICS = ("system_tokens", "total_tokens", "tool_count", "plan_steps",
                    "steps_completed", "steps_total",
                    "rounds", "response_chars", "duration_s")


def _mean_stderr(values: List[float]):
    n = len(values)
    if n == 0:
        return None
    mean = sum(values) / n
    if n == 1:
        return {"mean": round(mean, 2), "stderr": 0.0, "n": 1}
    var = sum((v - mean) ** 2 for v in values) / (n - 1)   # sample variance
    stderr = (var ** 0.5) / (n ** 0.5)
    return {"mean": round(mean, 2), "stderr": round(stderr, 2), "n": n}


def aggregate_runs(case: "Case", runs: List[CaseResult]) -> CaseResult:
    """Collapse N runs of one case into a single CaseResult with pass@k / pass^k + variance.

    Non-deterministic agents need multiple samples (2026 practice). We surface a
    REPRESENTATIVE run (a failing one if any, so triage/transcript show the failure) and
    attach `.repeat` with the aggregate stats; `overall_passed` then gates on pass^k.
    """
    if len(runs) == 1:
        return runs[0]

    n = len(runs)
    # VOID runs are dropped before computing pass@k/pass^k. Counting a run where the server
    # was down as a pass^k failure would mark a perfectly good case as a regression; counting
    # it as a pass would hide a real one. Neither is honest — so it isn't a sample at all.
    scored_runs = [r for r in runs if r.counts_toward_pass_rate]
    n_void = n - len(scored_runs)
    if not scored_runs:
        # Every attempt was voided: the case has no verdict this run.
        rep = runs[0]
        agg = CaseResult(
            case_id=rep.case_id, prompt=rep.prompt, passed=False,
            assertion_failures=rep.assertion_failures, judge_results=rep.judge_results,
            metrics=dict(rep.metrics), error=rep.error, lane=rep.lane,
            transcript=dict(rep.transcript), budget_warnings=list(rep.budget_warnings),
        )
        agg.repeat = {"n_runs": n, "n_scored": 0, "n_void": n_void, "n_passed": 0,
                      "pass_at_k": False, "pass_hat_k": False, "pass_rate": None,
                      "run_passes": [], "metric_stats": {}, "all_void": True}
        return agg

    passes = [r.overall_passed for r in scored_runs]
    n_passed = sum(passes)
    rep = next((r for r in scored_runs if not r.overall_passed), scored_runs[0])

    # Per-metric mean ± stderr across runs (only numeric metrics).
    metric_stats = {}
    for key in _NUMERIC_METRICS:
        vals = [r.metrics.get(key) for r in runs if isinstance(r.metrics.get(key), (int, float))]
        if vals:
            metric_stats[key] = _mean_stderr(vals)

    agg = CaseResult(
        case_id=rep.case_id, prompt=rep.prompt, passed=rep.passed,
        assertion_failures=rep.assertion_failures, judge_results=rep.judge_results,
        metrics=dict(rep.metrics), error=rep.error, lane=rep.lane,
        transcript=dict(rep.transcript),
    )
    agg.repeat = {
        "n_runs": n, "n_scored": len(scored_runs), "n_void": n_void, "n_passed": n_passed,
        "pass_at_k": n_passed >= 1,                      # ≥1 success (exploratory view)
        # Reliability gate over SCORED runs only. A void attempt is not a sample, so a case
        # that passed every run it was actually able to make still passes pass^k.
        "pass_hat_k": n_passed == len(scored_runs),
        # Rate over scored runs, for the same reason.
        "pass_rate": round(n_passed / len(scored_runs), 3),
        "run_passes": passes,
        "metric_stats": metric_stats,
    }
    return agg


def _first_record(path: Path) -> Optional[Dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    return None


def list_suites() -> List[str]:
    """Datasets the runner can actually execute (records with a `prompt`).

    Some .jsonl files under datasets/ belong to standalone probes with their own schema
    (intent_router.jsonl uses `query`/`expect_mode` and is read by intent_router_live.py).
    Returning those made `--all` crash on load, so they are separated out rather than
    silently dropped — see list_probe_datasets.
    """
    out = []
    for p in sorted(DATASETS_DIR.glob("*.jsonl")):
        rec = _first_record(p)
        if rec is not None and "prompt" in rec:
            out.append(p.stem)
    return out


def list_probe_datasets() -> List[str]:
    """Datasets under datasets/ that the runner cannot execute, with their owning probe.

    Surfaced in `--list` so they stay visible: an invisible dataset is one nobody notices
    has rotted.
    """
    out = []
    for p in sorted(DATASETS_DIR.glob("*.jsonl")):
        rec = _first_record(p)
        if rec is None or "prompt" not in rec:
            out.append(p.stem)
    return out


# ── Deterministic assertions (run on the dry-run Payload) ────────────────────


def check_assertions(expect: Dict[str, Any], payload) -> List[str]:
    """Return a list of human-readable failures (empty == all passed)."""
    fails: List[str] = []
    names = set(payload.tool_names)

    def _missing(req):
        return [t for t in req if t not in names]

    if "tools_include" in expect:
        miss = _missing(expect["tools_include"])
        if miss:
            fails.append(
                f"tools_include: missing {miss} "
                f"(bound: {sorted(names) or 'none'}) — tool/subagent not wired or RAG missed it")

    if "tools_exclude" in expect:
        leaked = [t for t in expect["tools_exclude"] if t in names]
        if leaked:
            fails.append(f"tools_exclude: unwanted {leaked} leaked into selection")

    if "tools_any" in expect:
        if not any(t in names for t in expect["tools_any"]):
            fails.append(f"tools_any: none of {expect['tools_any']} were bound")

    if "max_tools" in expect and payload.tool_count > expect["max_tools"]:
        fails.append(
            f"max_tools: {payload.tool_count} tools bound > {expect['max_tools']} "
            f"(context bloat from tool selection)")

    if "min_tools" in expect and payload.tool_count < expect["min_tools"]:
        fails.append(f"min_tools: only {payload.tool_count} tools bound < {expect['min_tools']}")

    if expect.get("tools_nonempty") and payload.tool_count == 0:
        fails.append("tools_nonempty: no tools bound for a non-trivial planning turn")

    min_tc = expect.get("min_tool_count")
    if min_tc is not None and payload.tool_count < min_tc:
        fails.append(f"min_tool_count: only {payload.tool_count} tools bound < {min_tc}")

    if "max_system_tokens" in expect and payload.system_tokens > expect["max_system_tokens"]:
        fails.append(
            f"max_system_tokens: {payload.system_tokens} > {expect['max_system_tokens']} "
            f"(system prompt bloat)")

    if "max_total_tokens" in expect and payload.total_tokens > expect["max_total_tokens"]:
        fails.append(f"max_total_tokens: {payload.total_tokens} > {expect['max_total_tokens']}")

    if "system_includes" in expect:
        for s in expect["system_includes"]:
            if s not in payload.system_prompt:
                fails.append(f"system_includes: '{s}' absent from system prompt")

    if "system_excludes" in expect:
        for s in expect["system_excludes"]:
            if s in payload.system_prompt:
                fails.append(f"system_excludes: '{s}' present but should not be")

    return fails


def scout_tools_from_timeline(timeline: List[str]) -> List[str]:
    """Tools executed during the scout phase (timeline entries before the plan snapshot)."""
    if not timeline:
        return []
    if "plan" in timeline:
        pre = timeline[: timeline.index("plan")]
    else:
        pre = timeline
    out: List[str] = []
    for entry in pre:
        if not entry.startswith("tool:"):
            continue
        name = entry.split(":", 1)[1]
        if name != "scout_gather":
            out.append(name)
    return out


def plan_tool_names(plan: Optional[List[Dict[str, Any]]]) -> set:
    """Collect all tool names referenced across plan steps."""
    names: set = set()
    for step in plan or []:
        for tool in step.get("tools") or []:
            if tool:
                names.add(tool)
    return names


# Constraint kinds `check_tool_args` understands — an unrecognized constraint key (typo) is
# flagged explicitly rather than silently no-op'd (see #309's dataset schema validation for
# the same principle applied to top-level expect keys).
_VALID_TOOL_ARG_CONSTRAINTS = {"equals", "contains", "not_empty", "min", "max", "regex"}


def _contains(actual: Any, needle: Any) -> bool:
    """substring-of-string OR membership/substring-in-any-item for list/tuple/set args
    (e.g. web_search's `queries: [...]` — 'contains a query about Seattle', not '== a string')."""
    if isinstance(actual, str):
        return needle in actual
    if isinstance(actual, (list, tuple, set)):
        return needle in actual or any(isinstance(a, str) and needle in a for a in actual)
    return False


def check_tool_args(constraints: List[Dict[str, Any]], tool_calls: List[Dict[str, Any]]) -> List[str]:
    """Component-layer check: not just WHICH tool was called, but WHAT it was called with.

    Each constraint: {"tool": "<name>", "arg": "<param>", <one or more of equals/contains/
    not_empty/min/max/regex>}. Checked against the LAST call to that tool (a tool retried with
    corrected args should be judged on what it ultimately did, not its first attempt).
    """
    fails: List[str] = []
    for c in constraints:
        tool, arg = c.get("tool"), c.get("arg")
        matches = [call for call in tool_calls if call.get("name") == tool]
        if not matches:
            fails.append(f"tool_args: '{tool}' was never called (cannot check arg '{arg}')")
            continue
        actual = (matches[-1].get("params") or {}).get(arg)

        unknown = set(c) - {"tool", "arg"} - _VALID_TOOL_ARG_CONSTRAINTS
        if unknown:
            fails.append(f"tool_args: unknown constraint key(s) {sorted(unknown)} on {tool}.{arg}")

        if "equals" in c and actual != c["equals"]:
            fails.append(f"tool_args: {tool}.{arg}={actual!r} != expected {c['equals']!r}")
        if "contains" in c and not _contains(actual, c["contains"]):
            fails.append(f"tool_args: {tool}.{arg}={actual!r} does not contain {c['contains']!r}")
        if c.get("not_empty") and not actual:
            fails.append(f"tool_args: {tool}.{arg}={actual!r} is empty (expected non-empty)")
        if "min" in c and (not isinstance(actual, (int, float)) or actual < c["min"]):
            fails.append(f"tool_args: {tool}.{arg}={actual!r} < min {c['min']}")
        if "max" in c and (not isinstance(actual, (int, float)) or actual > c["max"]):
            fails.append(f"tool_args: {tool}.{arg}={actual!r} > max {c['max']}")
        if "regex" in c and (not isinstance(actual, str) or not re.search(c["regex"], actual)):
            fails.append(f"tool_args: {tool}.{arg}={actual!r} does not match regex {c['regex']!r}")
    return fails


def check_live_assertions(expect: Dict[str, Any], trace) -> List[str]:
    """Assertions for the --live lane: run against ACTUAL output (executed tools, plan, response)."""
    fails: List[str] = []
    names = set(trace.tool_names)

    if "tools_include" in expect:
        miss = [t for t in expect["tools_include"] if t not in names]
        if miss:
            fails.append(f"tools_include: model never called {miss} (called: {sorted(names) or 'none'})")
    if "tools_any" in expect and not any(t in names for t in expect["tools_any"]):
        fails.append(f"tools_any: model called none of {expect['tools_any']}")
    if "tools_exclude" in expect:
        leaked = [t for t in expect["tools_exclude"] if t in names]
        if leaked:
            fails.append(f"tools_exclude: model wrongly called {leaked}")

    if "max_tool_repeats" in expect:
        from collections import Counter
        # Check for genuine runaway loops: identical tool calls with the EXACT SAME parameters.
        # Calling the same tool with different arguments (e.g. iterating over 5 courses or searching
        # different queries) is legitimate batch progress, not an infinite loop.
        call_signatures = []
        for tc in getattr(trace, "tool_calls", []):
            t_name = tc.get("name") if isinstance(tc, dict) else str(tc)
            if t_name in ("complete_step",):
                continue
            params = tc.get("params") if isinstance(tc, dict) else {}
            try:
                import json
                params_str = json.dumps(params, sort_keys=True, default=str)
            except Exception:
                params_str = str(params)
            call_signatures.append((t_name, params_str))

        if call_signatures:
            counts = Counter(call_signatures)
            over = {f"{t}({p[:60]}...)": c for (t, p), c in counts.items() if c > expect["max_tool_repeats"]}
            if over:
                fails.append(
                    f"max_tool_repeats: {over} called with identical arguments more than {expect['max_tool_repeats']}x "
                    f"(runaway repeat-call loop — see LoopDetector)"
                )

    steps = len(trace.plan) if trace.plan else 0
    if "min_steps" in expect and steps < expect["min_steps"]:
        fails.append(f"min_steps: plan has {steps} steps < {expect['min_steps']} (planner under-decomposed)")
    if "max_steps" in expect and steps > expect["max_steps"]:
        fails.append(f"max_steps: plan has {steps} steps > {expect['max_steps']} (planner over-decomposed)")

    if "max_duration_s" in expect and trace.duration_s > expect["max_duration_s"]:
        fails.append(f"max_duration_s: took {trace.duration_s}s > {expect['max_duration_s']}s "
                     f"(turn latency regression)")

    total_tokens = getattr(trace, "total_tokens", None)
    if "max_total_tokens" in expect and total_tokens is not None and total_tokens > expect["max_total_tokens"]:
        fails.append(f"max_total_tokens: ~{total_tokens} > {expect['max_total_tokens']} "
                     f"(estimated from reasoning + tool results + response)")

    resp = trace.final_response or ""
    min_chars = expect.get("min_response_chars")
    if min_chars is not None and len(resp) < min_chars:
        fails.append(f"min_response_chars: response is {len(resp)} chars < {min_chars} "
                     f"(likely a reasoning spiral that hit the completion cap with empty/truncated output — see #258)")
    for s in expect.get("response_includes", []):
        if s.lower() not in resp.lower():
            fails.append(f"response_includes: '{s}' absent from final response")
    for s in expect.get("response_excludes", []):
        if s.lower() in resp.lower():
            fails.append(f"response_excludes: '{s}' present in final response")

    if "tool_args" in expect:
        fails += check_tool_args(expect["tool_args"], getattr(trace, "tool_calls", []) or [])

    if "tool_results_regex_any" in expect:
        import re as _re
        blob = getattr(trace, "tool_results", "") or ""
        patterns = expect["tool_results_regex_any"] or []
        if not any(_re.search(p, blob) for p in patterns):
            fails.append(
                f"tool_results_regex_any: none of {patterns} matched tool_results "
                f"(likely never opened the required detail/conversation view — "
                f"list-preview answers are a hard fail)"
            )

    # ── Safety: destructive/side-effecting actions must pause for a human, not auto-execute ──
    if expect.get("requires_approval") and trace.stop_reason not in PAUSE_REASONS:
        fails.append(
            f"requires_approval: turn ended with stop_reason={trace.stop_reason!r} instead of "
            f"pausing for approval/clarification ({sorted(PAUSE_REASONS)}) — a side-effecting "
            f"action may have executed without human confirmation")

    # ── Workflow lane (--workflow): drove plan → approve → execute to completion ──
    if expect.get("must_complete") and not getattr(trace, "completed", False):
        done = getattr(trace, "steps_completed", 0)
        total = getattr(trace, "steps_total", 0)
        fails.append(
            f"must_complete: workflow did not finish (rounds={getattr(trace, 'rounds', '?')}, "
            f"progress={done}/{total} steps, last stop_reason={getattr(trace, 'stop_reason', None)}) — "
            f"stuck awaiting input, errored, or hit the round cap")
    if "min_steps_completed" in expect:
        done = getattr(trace, "steps_completed", 0)
        if done < expect["min_steps_completed"]:
            total = getattr(trace, "steps_total", 0)
            fails.append(
                f"min_steps_completed: only {done}/{total} plan steps were marked complete "
                f"< {expect['min_steps_completed']} required — the model executed but did not "
                f"advance the plan (lost the thread / never called complete_step)")

    rounds = getattr(trace, "rounds", 0)
    if "max_rounds" in expect and rounds > expect["max_rounds"]:
        fails.append(f"max_rounds: took {rounds} turns > {expect['max_rounds']} "
                     f"(too many pause/approve cycles)")
    if "min_rounds" in expect and rounds < expect["min_rounds"]:
        fails.append(f"min_rounds: only {rounds} turns < {expect['min_rounds']} "
                     f"(expected a plan-approval pause that never happened)")

    # ── Scout gather (plan lane V1) ───────────────────────────────────────────
    if expect.get("scout_before_plan"):
        timeline = getattr(trace, "timeline", []) or []
        if "plan" not in timeline:
            fails.append("scout_before_plan: no execution_plan snapshot in stream")
        else:
            plan_idx = timeline.index("plan")
            pre_plan = timeline[:plan_idx]
            if "tool:scout_gather" not in pre_plan:
                fails.append(
                    f"scout_before_plan: scout_gather did not appear before plan "
                    f"(pre-plan timeline={pre_plan})"
                )

    if expect.get("gather_brief_present") and not getattr(trace, "gather_brief", None):
        fails.append("gather_brief_present: plan snapshot had no gather_brief")

    min_brief = expect.get("gather_brief_min_chars")
    if min_brief is not None:
        brief = getattr(trace, "gather_brief", None) or ""
        if len(brief) < min_brief:
            fails.append(
                f"gather_brief_min_chars: brief has {len(brief)} chars < {min_brief}"
            )

    timeline = getattr(trace, "timeline", []) or []
    scout_tools = scout_tools_from_timeline(timeline)

    if "scout_tools_include" in expect:
        miss = [t for t in expect["scout_tools_include"] if t not in scout_tools]
        if miss:
            fails.append(f"scout_tools_include: scout phase never called {miss} (called: {scout_tools})")
    if "scout_tools_any" in expect and not any(t in scout_tools for t in expect["scout_tools_any"]):
        fails.append(f"scout_tools_any: scout phase called none of {expect['scout_tools_any']}")
    if "scout_tools_exclude" in expect:
        leaked = [t for t in expect["scout_tools_exclude"] if t in scout_tools]
        if leaked:
            fails.append(f"scout_tools_exclude: scout wrongly called {leaked}")

    max_scout = expect.get("max_scout_pre_plan_tools")
    if max_scout is not None and len(scout_tools) > max_scout:
        fails.append(
            f"max_scout_pre_plan_tools: scout called {len(scout_tools)} tools > {max_scout} "
            f"({scout_tools})"
        )

    plan = getattr(trace, "plan", None)
    planned = plan_tool_names(plan)
    if "plan_tools_include" in expect:
        miss = [t for t in expect["plan_tools_include"] if t not in planned]
        if miss:
            fails.append(f"plan_tools_include: plan never references {miss} (planned: {sorted(planned)})")
    if "plan_tools_any" in expect and not any(t in planned for t in expect["plan_tools_any"]):
        fails.append(f"plan_tools_any: plan references none of {expect['plan_tools_any']}")
    if "plan_tools_exclude" in expect:
        leaked = [t for t in expect["plan_tools_exclude"] if t in planned]
        if leaked:
            fails.append(f"plan_tools_exclude: plan wrongly references {leaked}")

    return fails


# ── Report rendering ─────────────────────────────────────────────────────────


def _prev_pass_rate(suite: Optional[str] = None, lane: Optional[str] = None) -> Optional[float]:
    """Pass rate of the previous COMPARABLE run.

    Previously this read the newest run_*.json regardless of suite, lane or judge model, so
    the "vs last run" delta could silently compare a dry-run tool_selection result against a
    judged live workflow one. Now it only compares like with like, and returns None rather
    than a misleading number when there is no comparable baseline.
    """
    try:
        from evals.scoreboard import trend
        t = trend(suite, lane=lane) if suite else None
        if t and t.points:
            return t.latest
    except Exception:
        pass
    return None


def render_report(suite: str, results: List[CaseResult], judged: bool, lane: str = "dry-run") -> str:
    summary = summarize(results)
    total = summary["total"]
    scored = summary["scored"]
    passed = summary["passed"]
    rate = summary["pass_rate"]
    prev = _prev_pass_rate(suite, lane)
    delta = (f" (delta {rate - prev:+.3f} vs previous comparable run)"
             if (prev is not None and rate is not None) else "")

    # Multi-run summary (only when --repeat N>1 was used).
    repeated = [r for r in results if r.repeat]
    repeat_line = ""
    if repeated:
        k = repeated[0].repeat["n_runs"]
        pass_at_k = sum(1 for r in repeated if r.repeat["pass_at_k"])
        pass_hat_k = sum(1 for r in repeated if r.repeat["pass_hat_k"])
        flaky = [r.case_id for r in repeated
                 if r.repeat["pass_at_k"] and not r.repeat["pass_hat_k"]]
        repeat_line = (f"Repeat    : {k}× per case | pass@{k} {pass_at_k}/{len(repeated)} | "
                       f"pass^{k} {pass_hat_k}/{len(repeated)}"
                       + (f" | FLAKY: {', '.join(flaky)}" if flaky else ""))

    lines = ["# HARNESS EVAL REPORT"]
    if any(r.metrics.get("prompts_offline") for r in results):
        lines += [
            "",
            "⚠️  WARNING: Supabase prompt warmup failed — this run used OFFLINE MOCK PROMPTS, not "
            "the real production system prompt. system_includes/system_excludes/prompt_contract "
            "results in this run are NOT representative of production. Fix Supabase connectivity "
            "and re-run before trusting any prompt-related pass/fail here.",
            "",
        ]
    lines += [
        f"Suite     : {suite}",
        f"Mode      : {lane}{' + judge' if judged else ' (free, no LLM)' if lane == 'dry-run' else ''}",
        f"Result    : {passed}/{scored} scored cases passed "
        f"({'n/a' if rate is None else f'{rate:.0%}'}){delta}",
    ]
    if summary["void"]:
        lines += [
            f"VOID      : {summary['void']}/{total} case(s) never fairly tested "
            f"(EXCLUDED from the pass rate above)",
        ]
    if repeat_line:
        lines.append(repeat_line)
        lines.append("  (gate = pass^k: a case passes only if ALL runs passed; flaky = some-but-not-all)")

    # ── VOID section ─────────────────────────────────────────────────────────
    # Printed BEFORE failures and on its own, because when this section is non-empty it is
    # almost always the only thing worth acting on: eight cases voided on
    # `google_oauth_expired` is one fix, not eight investigations.
    voids = [r for r in results if r.is_void]
    if voids:
        lines += ["", "## VOID - environment/harness broke, model was NOT fairly tested"]
        if not summary["trustworthy"]:
            lines += ["",
                      "**This run's pass rate is NOT comparable to a clean run.** More than 10% "
                      "of cases never reached a healthy environment, so the effective sample is "
                      "small and biased toward whatever still worked. Fix the causes below "
                      "(`python -m evals.preflight`) and re-run before drawing conclusions."]
        for reason, ids in summary["void_by_reason"].items():
            lines.append(f"\n### {reason} ({len(ids)} case(s))")
            lines.append(f"- cases: {', '.join(ids)}")
            example = next((r for r in voids if r.void_reason == reason and r.error), None)
            if example:
                lines.append(f"- example error: {example.error[:300]}")
            if reason == "judge_needs_rejudge":
                lines.append(
                    "- action: re-run `python -m evals.tools.rejudge_run --run <ts> --suite <suite>` "
                    "after fixing judge context (coalesce traces / cap evidence / raise "
                    "`EVAL_JUDGE_NUM_CTX`). Check each case's `diag.likely_cause`."
                )
                for r in voids:
                    if r.void_reason != reason:
                        continue
                    for j in r.judge_results:
                        if not j.get("needs_rejudge"):
                            continue
                        diag = j.get("diag") or {}
                        lines.append(
                            f"- `{r.case_id}` / {j.get('rubric')}: "
                            f"likely_cause={diag.get('likely_cause')} "
                            f"user_chars={diag.get('user_chars')} "
                            f"num_ctx={diag.get('num_ctx')}"
                        )

    lines += ["", "## FAILURES (act on these)"]
    failures = [r for r in results if r.outcome == FAIL]
    if not failures:
        lines.append("None — every scored case passed."
                     + (" (Note: some cases were VOID — see above.)" if voids else ""))
    for r in failures:
        # Latency-only failures conflate "slow" with "wrong" if not called out separately --
        # a case that only tripped max_duration_s (tool selection + judges otherwise clean)
        # says nothing about correctness. Flag it so a human doesn't have to open the
        # transcript just to learn the model actually did the right thing, slowly.
        judges_ok = all(
            j.get("passed", True) and not j.get("needs_rejudge")
            for j in r.judge_results
        )
        only_latency = (not r.error and judges_ok and r.assertion_failures
                        and all(f.startswith("max_duration_s") for f in r.assertion_failures))
        tag = "  [LATENCY-ONLY — tool selection/judges were clean, only the duration budget was missed]" if only_latency else ""
        lines.append(f"\n### {r.case_id} — {r.prompt!r}{tag}")
        if r.repeat:
            rp = r.repeat
            lines.append(f"- STABILITY: {rp['n_passed']}/{rp['n_runs']} runs passed "
                         f"(pass@k={rp['pass_at_k']}, pass^k={rp['pass_hat_k']}) — "
                         f"{'FLAKY (non-deterministic)' if rp['pass_at_k'] and not rp['pass_hat_k'] else 'consistently failing'}")
        if r.error:
            lines.append(f"- ERROR: {r.error}")
        for f in r.assertion_failures:
            lines.append(f"- ASSERT: {f}")
        for j in r.judge_results:
            if not j.get("passed", True):
                reasons = "; ".join(j.get("reasons", [])) or "(no reason given)"
                lines.append(f"- JUDGE[{j['rubric']}] score={j.get('score')}: {reasons}")
        m = r.metrics
        if m:
            tools = m.get('tool_names', [])
            parts = [f"{m.get('tool_count')} tools ({', '.join(tools[:8])}{'…' if len(tools) > 8 else ''})"]
            if "system_tokens" in m:        # dry-run lane
                parts.append(f"sys={m.get('system_tokens')}tok total={m.get('total_tokens')}tok")
            if "plan_steps" in m:           # live / workflow lane
                parts.append(f"plan={m.get('plan_steps')}steps resp={m.get('response_chars')}ch "
                             f"{m.get('duration_s')}s ~{m.get('total_tokens')}tok")
            if "rounds" in m:               # workflow lane
                parts.append(f"rounds={m.get('rounds')} completed={m.get('completed')} "
                             f"stop={m.get('stop_reason')}")
            lines.append("- metrics: " + ", ".join(parts))
        if r.repeat and r.repeat.get("metric_stats"):
            stats = r.repeat["metric_stats"]
            shown = [f"{k}={v['mean']}±{v['stderr']}" for k, v in stats.items()
                     if v["stderr"] > 0][:5]
            if shown:
                lines.append("- variance (mean±stderr over runs): " + ", ".join(shown))
        if r.budget_warnings:
            lines.append("- budget (advisory, NOT the reason this failed): "
                         + "; ".join(r.budget_warnings))

    # ── Advisory budget breaches on cases that PASSED ─────────────────────────
    # Deliberately separate from failures. A correct-but-slow turn is a perf signal to
    # trend, not a red case — conflating the two is what made `max_duration_s` useless.
    budget_only = [r for r in results if r.outcome == PASS and r.budget_warnings]
    if budget_only:
        lines += ["", "## BUDGET NOTES (passed, but outside an advisory budget)", "",
                  "These are cost/shape signals for trending. They did not fail anything."]
        for r in budget_only:
            lines.append(f"- `{r.case_id}`: " + "; ".join(r.budget_warnings))

    lines += ["", "## PASSED", ""]
    lines.append(", ".join(r.case_id for r in results if r.outcome == PASS) or "(none)")
    return "\n".join(lines)


# ── Transcript persistence (full trajectory per case, for failure review) ─────


def _verdict_emoji(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _plan_step_progress(plan) -> tuple:
    """(completed, total) from a plan snapshot — mirrors live._plan_progress without importing it
    (live.py imports core.py, so the dependency can only go one way)."""
    if not plan:
        return (0, 0)
    total = len(plan)
    done = sum(1 for s in plan if isinstance(s, dict) and s.get("status") == "completed")
    return (done, total)


def _head_tail(text: str, limit: int = 3000) -> str:
    """Keep the head AND tail of a long blob. For a stalled multi-round run the END of the
    reasoning (where it lost the thread / gave up) matters more than the start — a plain
    head-only truncation hid exactly the part you need.

    ``limit <= 0`` means keep everything (HITL / quality transcripts).
    """
    text = text or ""
    if limit is None or limit <= 0 or len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n\n… [{len(text) - limit} chars elided] …\n\n{text[-half:]}"


def _render_events_chronological(events: List[Dict[str, Any]]) -> List[str]:
    """thinking → tool_start(+end) → thinking … in wire order.

    Also coalesces consecutive same-agent thinking events as a render-time safety net
    (in case a legacy trace was captured before ``_record_thinking`` merged deltas).
    """
    # Coalesce consecutive thinking deltas for the same agent before rendering.
    coalesced: List[Dict[str, Any]] = []
    for ev in events or []:
        ev = ev or {}
        if (
            ev.get("type") == "thinking"
            and coalesced
            and coalesced[-1].get("type") == "thinking"
            and (coalesced[-1].get("agent") or "Main") == (ev.get("agent") or "Main")
        ):
            coalesced[-1] = {
                **coalesced[-1],
                "text": (coalesced[-1].get("text") or "") + (ev.get("text") or ""),
            }
        else:
            coalesced.append(dict(ev))

    L = ["## Trajectory (chronological)", ""]
    i = 0
    n = len(coalesced)
    while i < n:
        ev = coalesced[i] or {}
        et = ev.get("type")
        if et == "thinking":
            agent = ev.get("agent") or "Main"
            L += [f"### Thinking ({agent})", "```", ev.get("text") or "", "```", ""]
        elif et == "tool_start":
            name = ev.get("name") or "?"
            params = json.dumps(ev.get("params") or {}, indent=2, default=str)
            result = None
            if (
                i + 1 < n
                and (coalesced[i + 1] or {}).get("type") == "tool_end"
                and (coalesced[i + 1] or {}).get("name") == name
            ):
                result = (coalesced[i + 1] or {}).get("result")
                i += 1
            L += [f"### Tool `{name}`", "**params:**", "```json", params, "```"]
            if result is not None:
                L += ["**result:**", "```", str(result), "```"]
            L.append("")
        elif et == "tool_end":
            L += [
                f"### Tool result `{ev.get('name') or '?'}`",
                "```",
                str(ev.get("result") or ""),
                "```",
                "",
            ]
        elif et == "plan":
            L += [
                "### Plan event",
                "```json",
                json.dumps({"goal": ev.get("goal"), "plan": ev.get("plan")}, indent=2, default=str),
                "```",
                "",
            ]
        else:
            L += [f"### Event `{et}`", "```json", json.dumps(ev, indent=2, default=str), "```", ""]
        i += 1
    return L


def render_transcript(case: "Case", result: CaseResult) -> str:
    """One human-readable markdown trajectory for a single case — what the model saw and did."""
    t = result.transcript or {}
    L = [
        f"# {result.case_id} — {_verdict_emoji(result.overall_passed)}",
        f"**Lane:** {result.lane}  |  **Category:** {case.category}",
        "",
        "## Prompt",
        f"> {result.prompt}",
        "",
    ]

    if result.repeat:
        rp = result.repeat
        marks = "".join("." if p else "x" for p in rp["run_passes"])
        L += [f"## Repeat: {rp['n_passed']}/{rp['n_runs']} passed  "
              f"(pass@k={rp['pass_at_k']}, pass^k={rp['pass_hat_k']})  [{marks}]",
              "_(. = pass, x = fail; trajectory below is a representative run)_", ""]

    if result.error:
        L += ["## ERROR", f"```\n{result.error}\n```", ""]

    # What the model received / produced (lane-dependent)
    if "system_prompt" in t:                      # dry-run lane
        L += ["## Bound tools", ", ".join(t.get("tool_names") or []) or "(none)",
              "", f"**System tokens:** {t.get('system_tokens')}  |  **Total tokens:** {t.get('total_tokens')}",
              "", "## System prompt (assembled)",
              "```", t.get("system_prompt") or "", "```", ""]
    if t.get("goal"):                             # workflow — the north-star objective
        L += ["## Goal (as the plan lane recorded it)", f"> {t.get('goal')}", ""]
    if "plan" in t and t.get("plan"):             # live / workflow
        L += ["## Plan proposed", "```json", json.dumps(t.get("plan"), indent=2), "```", ""]
    if "rounds" in t:                             # workflow
        done, total = _plan_step_progress(t.get("final_plan") or t.get("plan"))
        L += [f"## Workflow: {t.get('rounds')} round(s), completed={t.get('completed')}, "
              f"steps {done}/{total}, stop_reason={t.get('stop_reason')}", ""]
    # The WS stream only sees top-level turns; the real nested work (subagent turns + their
    # internal searches/tools) comes from LangSmith. Render it high, right under the summary.
    if t.get("langsmith"):
        L += [t.get("langsmith"), ""]
    # Per-round progress ledger — turns "ran out of rounds" into a legible trail of what each
    # round did and whether the plan actually advanced (the signal for a run that lost the thread).
    if t.get("rounds_detail"):
        L += ["## Round-by-round progress",
              "| Round | Steps done | Active step | Tools called | stop_reason |",
              "|---|---|---|---|---|"]
        for rd in t["rounds_detail"]:
            tools = ", ".join(rd.get("tools") or []) or "—"
            active = (rd.get("active_step") or "—").replace("|", "\\|")
            L.append(f"| {rd.get('round')} | {rd.get('steps_completed')}/{rd.get('steps_total')} "
                     f"| {active} | {tools} | {rd.get('stop_reason') or '—'} |")
        L.append("")
    # Final plan state (evolved statuses) — distinct from the proposed plan above. Shows which
    # steps actually completed vs. where it stalled.
    if t.get("final_plan") and t.get("final_plan") != t.get("plan"):
        fp_lines = [f"- [{(s.get('status') or 'pending').upper()}] {s.get('id')}: {s.get('description')}"
                    for s in t["final_plan"] if isinstance(s, dict)]
        L += ["## Final plan state"] + fp_lines + [""]

    events = t.get("events") or []
    if events and result.lane != "dry-run":
        L += _render_events_chronological(events)
    elif result.lane != "dry-run":
        # Legacy fallback (pre-events traces): keep sections but do NOT truncate bodies.
        if "tool_names" in t:
            calls = t.get("tool_calls") or []
            if calls:
                L += ["## Tools executed"]
                for c in calls:
                    params_str = json.dumps(c.get("params") or {}, default=str)
                    L.append(f"- `{c.get('name')}` {params_str}")
                L.append("")
            else:
                L += ["## Tools executed", ", ".join(t.get("tool_names") or []) or "(none)", ""]
        if t.get("tool_results"):
            L += ["## Tool results", "```", t.get("tool_results") or "", "```", ""]
        by_agent = t.get("reasoning_by_agent") or {}
        if len(by_agent) > 1:
            L += ["## Reasoning by agent"]
            for agent, text in by_agent.items():
                L += [f"### {agent}", "```", text or "", "```", ""]
        elif t.get("reasoning"):
            L += ["## Reasoning", "```", t.get("reasoning") or "", "```", ""]

    if t.get("response") is not None:
        L += ["## Final response", t.get("response") or "(empty)", ""]

    # Grading
    if result.assertion_failures:
        L += ["## Assertion failures"] + [f"- {f}" for f in result.assertion_failures] + [""]
    if result.judge_results:
        L += ["## Judge verdicts"]
        for j in result.judge_results:
            score = j.get("score")
            if j.get("needs_rejudge"):
                mark = "NEEDS_REJUDGE"
            else:
                mark = _verdict_emoji(j.get("passed", True))
            L.append(f"- **{j['rubric']}** [{mark}] score={score}")
            for reason in j.get("reasons", []):
                L.append(f"    - {reason}")
            diag = j.get("diag") or {}
            if diag.get("likely_cause"):
                L.append(f"    - likely_cause=`{diag['likely_cause']}` "
                         f"(user_chars={diag.get('user_chars')}, "
                         f"est_tok≈{diag.get('est_input_tokens')}, "
                         f"num_ctx={diag.get('num_ctx')})")
        L.append("")

    if result.metrics:
        L += ["## Metrics", "```json", json.dumps(result.metrics, indent=2), "```"]
    return "\n".join(L)


def persist_transcripts(run_ts: str, cases: List["Case"], results: List[CaseResult]) -> Path:
    """Write one markdown (+ raw JSON sidecar) per case under runs/transcripts/run_<ts>/."""
    tdir = RUNS_DIR / "transcripts" / f"run_{run_ts}"
    tdir.mkdir(parents=True, exist_ok=True)
    by_id = {c.id: c for c in cases}
    for r in results:
        case = by_id.get(r.case_id) or Case(id=r.case_id, prompt=r.prompt)
        safe = re.sub(r'[<>:"/\\|?*]', "_", r.case_id)  # Windows-illegal filename chars
        (tdir / f"{safe}.md").write_text(render_transcript(case, r), encoding="utf-8")
        # Raw package for the quality judge — never truncated, never markdown-shaped.
        raw = {
            "id": r.case_id,
            "prompt": r.prompt,
            "category": case.category,
            "lane": r.lane,
            "passed": r.overall_passed,
            "metrics": r.metrics,
            "assertion_failures": r.assertion_failures,
            "judge_results": r.judge_results,
            "error": r.error,
            "transcript": r.transcript or {},
        }
        (tdir / f"{safe}.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
    return tdir


def read_latest_transcripts(workspace_root: "Path | str", failing_only: bool = True,
                            char_budget: int = 40000) -> str:
    """Read back the per-case trajectories from the most recent run under `workspace_root`.

    `persist_transcripts` writes one markdown file per case under
    `<root>/evals/runs/transcripts/run_<ts>/` — each carries the model's reasoning (head+tail),
    every tool call with params, the plan, and the round-by-round ledger. This finds the newest
    such dir and returns those trajectories concatenated (failing cases first), capped at
    `char_budget`.

    Purpose: the self-eval loop (issue #358) hands the coder the *report* summary — which
    assertion failed — but the summary does not say WHY the model went wrong, especially in its
    thinking. That lives in the transcript. This is how the bridge/probe attach it.

    Takes the workspace root as an argument (not RUNS_DIR) precisely because the self-eval bridge
    runs the eval inside the coder's *edited clone*, so the transcripts to read are under that
    clone, not the host checkout. Returns "" for lanes that write no per-case transcripts
    (tool_contract/prompt_audit/replay) — those need no reasoning trail.
    """
    troot = Path(workspace_root) / "evals" / "runs" / "transcripts"
    if not troot.is_dir():
        return ""
    run_dirs = sorted((d for d in troot.glob("run_*") if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
    if not run_dirs:
        return ""
    files = sorted(run_dirs[0].glob("*.md"))
    fails: List[str] = []
    passes: List[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — one unreadable file must not sink the rest
            continue
        head = text.splitlines()[0] if text else ""
        (fails if "— FAIL" in head else passes).append(text)
    ordered = fails if failing_only else fails + passes
    if not ordered:
        return ""

    out: List[str] = []
    used = 0
    for t in ordered:
        if used + len(t) > char_budget:
            remaining = char_budget - used
            if remaining > 500:
                out.append(t[:remaining] + "\n\n… [transcript truncated — budget reached] …")
            break
        out.append(t)
        used += len(t)
    return ("## Full failing-case trajectories (reasoning + tool calls + plan)\n\n"
            + "\n\n---\n\n".join(out))
