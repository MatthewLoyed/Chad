r"""
scoreboard.py — durable longitudinal tracking: are we actually getting better?

WHY THIS EXISTS
---------------
Before this, `evals/runs/` was entirely gitignored and the only historical comparison was
`_prev_pass_rate()`, which read the single most recent run file — of *any* suite, in *any*
lane, under *any* judge model. So "78% (was 84%)" could mean a real regression, or it could
mean the previous file happened to be a different suite. That is worse than no baseline,
because it invites confident wrong conclusions.

This module keeps an append-only, git-tracked `history.jsonl`: one compact record per run,
carrying the run's summary AND its manifest (git SHA, models, dataset hashes). From that it
answers the questions that actually drive work:

  * Is a suite's pass rate trending up, and is the change bigger than the noise?
  * Which cases REGRESSED (green for several runs, now red)? That's the actionable list.
  * Which cases are FLAKY (flipping between runs)? A flaky case is not evidence of anything
    and should be fixed or quarantined before it's used to justify a change.
  * Which cases are saturated (green forever)? Per Anthropic's guidance those give only
    regression signal and should graduate out of the capability suite.
  * Which tools/capabilities have NO eval coverage at all?

Comparability is enforced, not assumed: a trend point is only compared against a baseline
with the same suite, lane and judge model, and runs that voided >10% of their cases are
recorded but excluded from trend math.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

EVALS_DIR = Path(__file__).resolve().parent
RUNS_DIR = EVALS_DIR / "runs"
DATASETS_DIR = EVALS_DIR / "datasets"
HISTORY_PATH = RUNS_DIR / "history.jsonl"

# A case must have been green this many consecutive runs before a red counts as a
# REGRESSION rather than ordinary noise on an unstable case.
_REGRESSION_GREEN_STREAK = 2
# Green for this many consecutive runs = saturated; it now only provides regression signal.
_SATURATED_STREAK = 5
# Cases whose verdict flips at least this often are treated as flaky, not as signal.
_FLAKY_FLIP_RATE = 0.25


# ── Run manifest ─────────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=EVALS_DIR.parent, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def dataset_digest(suites: Iterable[str]) -> Dict[str, str]:
    """Short content hash per suite file, so a pass-rate change can be attributed to a
    dataset edit rather than to the model."""
    out: Dict[str, str] = {}
    for suite in suites:
        p = DATASETS_DIR / f"{suite}.jsonl"
        if p.exists():
            out[suite] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    return out


def build_manifest(*, suites: Iterable[str], lane: str, judge_model: Optional[str],
                   judged: bool, repeat: int = 1,
                   preflight: Optional[Dict[str, Any]] = None,
                   brain_model: Optional[str] = None) -> Dict[str, Any]:
    """Everything needed to decide, months later, whether two runs are comparable."""
    dirty = bool(_git("status", "--porcelain"))
    return {
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        # An uncommitted working tree means this result is not reproducible from the SHA.
        "git_dirty": dirty,
        "lane": lane,
        "judged": judged,
        "judge_model": judge_model,
        "brain_model": brain_model,
        "repeat": repeat,
        "suites": sorted(suites),
        "dataset_digest": dataset_digest(suites),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "preflight": preflight or {},
    }


def comparability_key(manifest: Dict[str, Any], suite: str) -> Tuple:
    """Two runs are only comparable on the axes that change what's being measured."""
    return (suite, manifest.get("lane"), manifest.get("judged"),
            manifest.get("judge_model"), manifest.get("brain_model"))


# ── History ──────────────────────────────────────────────────────────────────


def append_history(run_payload: Dict[str, Any]) -> None:
    """Append one compact record. Never rewrites — history is append-only so a bad run can
    be explained later rather than quietly disappearing."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    summary = run_payload.get("summary", {})
    manifest = run_payload.get("manifest", {})
    record = {
        "timestamp": run_payload.get("timestamp"),
        "suite": run_payload.get("suite"),
        "lane": run_payload.get("lane"),
        "judged": run_payload.get("judged"),
        "summary": {k: summary.get(k) for k in
                    ("total", "scored", "passed", "failed", "void", "pass_rate",
                     "void_rate", "void_by_reason", "trustworthy", "flaky")},
        "manifest": {k: manifest.get(k) for k in
                     ("git_sha", "git_branch", "git_dirty", "judge_model", "brain_model",
                      "repeat", "dataset_digest")},
        # Per-case verdicts: the substrate for regression/flakiness detection.
        "cases": {c["id"]: c.get("outcome", "pass" if c.get("passed") else "fail")
                  for c in run_payload.get("cases", [])},
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def load_history(suite: Optional[str] = None, lane: Optional[str] = None,
                 limit: int = 0) -> List[Dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if suite and rec.get("suite") != suite:
            continue
        if lane and rec.get("lane") != lane:
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get("timestamp") or "")
    return out[-limit:] if limit else out


# ── Per-case history analysis ────────────────────────────────────────────────


@dataclass
class CaseHistory:
    case_id: str
    outcomes: List[str] = field(default_factory=list)     # oldest -> newest
    timestamps: List[str] = field(default_factory=list)

    @property
    def scored(self) -> List[str]:
        """Void outcomes are not verdicts — they must not be read as failures here either."""
        return [o for o in self.outcomes if o in ("pass", "fail")]

    @property
    def latest(self) -> Optional[str]:
        return self.scored[-1] if self.scored else None

    @property
    def green_streak(self) -> int:
        n = 0
        for o in reversed(self.scored):
            if o != "pass":
                break
            n += 1
        return n

    @property
    def prior_green_streak(self) -> int:
        """Consecutive passes immediately BEFORE the latest verdict."""
        scored = self.scored[:-1]
        n = 0
        for o in reversed(scored):
            if o != "pass":
                break
            n += 1
        return n

    @property
    def flip_count(self) -> int:
        s = self.scored
        return sum(1 for a, b in zip(s, s[1:]) if a != b)

    @property
    def flip_rate(self) -> float:
        s = self.scored
        if len(s) < 2:
            return 0.0
        return self.flip_count / (len(s) - 1)

    @property
    def is_regression(self) -> bool:
        return (self.latest == "fail"
                and self.prior_green_streak >= _REGRESSION_GREEN_STREAK
                and not self.is_flaky)

    @property
    def is_flaky(self) -> bool:
        """Flaky means it goes BACK AND FORTH, which needs at least two transitions.

        A single green->red transition has a flip_rate that can easily exceed the threshold
        on a short history (pass,pass,pass,fail = 0.33), but that is the textbook shape of a
        regression, not of flakiness. Requiring >=2 flips is what keeps real regressions from
        being dismissed as noise.
        """
        return (len(self.scored) >= 4
                and self.flip_count >= 2
                and self.flip_rate >= _FLAKY_FLIP_RATE)

    @property
    def is_saturated(self) -> bool:
        return self.green_streak >= _SATURATED_STREAK

    @property
    def never_passed(self) -> bool:
        return bool(self.scored) and "pass" not in self.scored

    @property
    def void_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.startswith("void")) / len(self.outcomes)


def case_histories(suite: Optional[str] = None, lane: Optional[str] = None,
                   limit: int = 0) -> Dict[str, CaseHistory]:
    hist = load_history(suite=suite, lane=lane, limit=limit)
    out: Dict[str, CaseHistory] = {}
    for rec in hist:
        for cid, outcome in (rec.get("cases") or {}).items():
            ch = out.setdefault(cid, CaseHistory(cid))
            ch.outcomes.append(outcome)
            ch.timestamps.append(rec.get("timestamp") or "")
    return out


# ── Trend ────────────────────────────────────────────────────────────────────


@dataclass
class Trend:
    suite: str
    lane: str
    points: List[Tuple[str, float]] = field(default_factory=list)   # (timestamp, pass_rate)
    excluded_untrustworthy: int = 0

    @property
    def latest(self) -> Optional[float]:
        return self.points[-1][1] if self.points else None

    @property
    def previous(self) -> Optional[float]:
        return self.points[-2][1] if len(self.points) >= 2 else None

    @property
    def delta(self) -> Optional[float]:
        if self.latest is None or self.previous is None:
            return None
        return round(self.latest - self.previous, 3)

    @property
    def best(self) -> Optional[float]:
        return max((p for _, p in self.points), default=None)

    def sparkline(self) -> str:
        """ASCII only — this renders to a cp1252 Windows console."""
        if not self.points:
            return ""
        ramp = ".:-=+*#"
        lo = min(p for _, p in self.points)
        hi = max(p for _, p in self.points)
        span = (hi - lo) or 1.0
        return "".join(ramp[min(len(ramp) - 1, int((p - lo) / span * (len(ramp) - 1)))]
                       for _, p in self.points)


def trend(suite: str, lane: Optional[str] = None, limit: int = 0) -> Trend:
    """Pass-rate trend for one suite, excluding runs that weren't trustworthy."""
    recs = load_history(suite=suite, lane=lane, limit=limit)
    t = Trend(suite=suite, lane=lane or "*")
    for rec in recs:
        s = rec.get("summary") or {}
        rate = s.get("pass_rate")
        if rate is None:
            continue
        if not s.get("trustworthy", True):
            t.excluded_untrustworthy += 1
            continue
        t.points.append((rec.get("timestamp") or "", float(rate)))
    return t


# ── Coverage ─────────────────────────────────────────────────────────────────


@dataclass
class Coverage:
    covered_tools: List[str] = field(default_factory=list)
    uncovered_tools: List[str] = field(default_factory=list)
    unknown_tools: List[str] = field(default_factory=list)
    total_registered: int = 0
    cases_by_category: Dict[str, int] = field(default_factory=dict)
    suites_without_negative_cases: List[str] = field(default_factory=list)

    @property
    def coverage_pct(self) -> Optional[float]:
        if not self.total_registered:
            return None
        return round(100 * len(self.covered_tools) / self.total_registered, 1)


_TOOL_KEYS = ("tools_include", "tools_any", "scout_tools_include", "scout_tools_any",
              "plan_tools_include", "plan_tools_any")


def coverage() -> Coverage:
    """Which tools does the eval suite actually exercise?

    This is the answer to "what should I write an eval for next": a registered tool with
    zero cases has never been verified to be selectable at all. It also flags suites with no
    NEGATIVE cases (`tools_exclude`), since a suite that only ever asserts "call this tool"
    can be satisfied by a model that calls everything.
    """
    from evals.core import list_suites, load_suite
    from evals.preflight import _registered_tool_names, _tool_exists

    cov = Coverage()
    referenced: set = set()
    by_category: Dict[str, int] = defaultdict(int)
    has_negative: Dict[str, bool] = {}

    for suite in list_suites():
        try:
            cases = load_suite(suite)
        except Exception:
            continue
        has_negative.setdefault(suite, False)
        for case in cases:
            by_category[case.category] += 1
            for key in _TOOL_KEYS:
                referenced.update(case.expect.get(key, []) or [])
            if case.expect.get("tools_exclude") or case.expect.get("response_excludes"):
                has_negative[suite] = True

    cov.cases_by_category = dict(sorted(by_category.items()))
    cov.suites_without_negative_cases = sorted(s for s, v in has_negative.items() if not v)

    registered = _registered_tool_names()
    if registered:
        cov.total_registered = len(registered)
        cov.covered_tools = sorted(t for t in referenced if t in registered)
        cov.uncovered_tools = sorted(registered - referenced)
        cov.unknown_tools = sorted(t for t in referenced
                                   if not _tool_exists(t, registered))
    else:
        cov.covered_tools = sorted(referenced)
    return cov


# ── Rendering ────────────────────────────────────────────────────────────────


def render_scoreboard(suite: Optional[str] = None, lane: Optional[str] = None,
                      limit: int = 0) -> str:
    lines: List[str] = ["# Eval scoreboard", ""]
    hist = load_history(suite=suite, lane=lane, limit=limit)
    if not hist:
        lines += ["_No history yet._ It accumulates automatically: every `evals.runner` run "
                  "appends one record to `evals/runs/history.jsonl` (git-tracked).", ""]
        return "\n".join(lines)

    lines += [f"{len(hist)} run(s) recorded"
              + (f" for suite `{suite}`" if suite else " across all suites")
              + (f", lane `{lane}`" if lane else "") + ".", ""]

    # Per-suite trend.
    suites = sorted({r.get("suite") for r in hist if r.get("suite")})
    lines += ["## Pass-rate trend", "",
              "| suite | lane | runs | trend | latest | prev | delta | best | untrusted |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s in suites:
        for ln in sorted({r.get("lane") for r in hist if r.get("suite") == s}):
            t = trend(s, lane=ln, limit=limit)
            if not t.points:
                continue
            d = t.delta
            arrow = "" if d is None else (" up" if d > 0 else " DOWN" if d < 0 else " flat")
            lines.append(
                f"| {s} | {ln} | {len(t.points)} | `{t.sparkline()}` | "
                f"{t.latest:.3f} | {'-' if t.previous is None else f'{t.previous:.3f}'} | "
                f"{'-' if d is None else f'{d:+.3f}{arrow}'} | "
                f"{'-' if t.best is None else f'{t.best:.3f}'} | {t.excluded_untrustworthy} |")
    lines.append("")

    # Per-case classification — the actionable part.
    ch = case_histories(suite=suite, lane=lane, limit=limit)
    regressions = sorted(c for c, h in ch.items() if h.is_regression)
    flaky = sorted(c for c, h in ch.items() if h.is_flaky)
    saturated = sorted(c for c, h in ch.items() if h.is_saturated)
    never = sorted(c for c, h in ch.items() if h.never_passed)
    void_heavy = sorted(c for c, h in ch.items() if h.void_rate >= 0.25)

    lines += ["## Action list", ""]
    if regressions:
        lines += [f"### REGRESSED ({len(regressions)}) - fix these first", "",
                  "Green for at least "
                  f"{_REGRESSION_GREEN_STREAK} consecutive runs, now failing, and not flaky.", ""]
        for c in regressions:
            h = ch[c]
            lines.append(f"- `{c}` - was green x{h.prior_green_streak}, "
                         f"now fail (flip-rate {h.flip_rate:.2f})")
        lines.append("")
    else:
        lines += ["### REGRESSED (0)", "", "No case went from a stable green to red.", ""]

    if flaky:
        lines += [f"### FLAKY ({len(flaky)}) - not evidence of anything yet", "",
                  "These flip between runs, so neither a pass nor a fail here should be used "
                  "to justify a change. Fix the case or the nondeterminism, or quarantine it.", ""]
        for c in flaky:
            h = ch[c]
            lines.append(f"- `{c}` - flip-rate {h.flip_rate:.2f} over {len(h.scored)} runs "
                         f"({''.join('P' if o == 'pass' else 'F' for o in h.scored[-12:])})")
        lines.append("")

    if never:
        lines += [f"### NEVER PASSED ({len(never)})", "",
                  "A case no model has ever passed is usually a broken or ambiguous task "
                  "spec, not a capability gap - verify a reference solution exists before "
                  "trusting it as signal.", ""]
        lines += [f"- `{c}` ({len(ch[c].scored)} attempt(s))" for c in never] + [""]

    if void_heavy:
        lines += [f"### CHRONICALLY VOIDED ({len(void_heavy)})", "",
                  "These are voided by infrastructure a quarter of the time or more - they "
                  "cost run time and yield no signal. Fix the dependency or drop them.", ""]
        lines += [f"- `{c}` - voided {ch[c].void_rate:.0%} of runs" for c in void_heavy] + [""]

    if saturated:
        lines += [f"### SATURATED ({len(saturated)}) - graduate to regression-only", "",
                  f"Green {_SATURATED_STREAK}+ runs straight. These now provide regression "
                  f"signal only; write harder cases to keep measuring improvement.", ""]
        lines += [f"- `{c}` (green x{ch[c].green_streak})" for c in saturated] + [""]

    return "\n".join(lines)


def render_coverage(cov: Optional[Coverage] = None) -> str:
    cov = cov or coverage()
    lines = ["# Eval coverage", ""]
    if cov.total_registered:
        lines += [f"**{len(cov.covered_tools)}/{cov.total_registered} registered tools "
                  f"({cov.coverage_pct}%) are referenced by at least one eval case.**", ""]
    lines += ["## Cases by category", ""]
    lines += [f"- `{k}`: {v}" for k, v in cov.cases_by_category.items()] + [""]

    if cov.unknown_tools:
        lines += ["## Tool names that resolve to nothing", "",
                  "Referenced by a case but absent from the registry and from every known "
                  "dynamic namespace - either a rename that datasets never followed, or a "
                  "typo. In `tools_include` this makes the case permanently unpassable.", ""]
        lines += [f"- `{t}`" for t in cov.unknown_tools] + [""]

    if cov.uncovered_tools:
        lines += [f"## Zero-coverage tools ({len(cov.uncovered_tools)})", "",
                  "Registered and selectable, but no eval case has ever asserted on them. "
                  "This is the backlog for 'what eval should I write next'.", ""]
        lines += [f"- `{t}`" for t in cov.uncovered_tools] + [""]

    if cov.suites_without_negative_cases:
        lines += ["## Suites with no negative cases", "",
                  "Every case asserts what the model SHOULD do and none assert what it should "
                  "NOT - a suite like this is satisfiable by a model that over-calls tools. "
                  "Add `tools_exclude` / `response_excludes` cases.", ""]
        lines += [f"- `{s}`" for s in cov.suites_without_negative_cases] + [""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Eval scoreboard: trends, regressions, coverage.")
    ap.add_argument("--suite", "-s", help="restrict to one suite")
    ap.add_argument("--lane", help="restrict to one lane (dry-run/live/workflow)")
    ap.add_argument("--last", type=int, default=0, metavar="N",
                    help="only consider the last N runs")
    ap.add_argument("--coverage", action="store_true", help="show the coverage report instead")
    ap.add_argument("--write", action="store_true",
                    help="also write evals/runs/scoreboard.md / coverage.md")
    args = ap.parse_args(argv)

    if args.coverage:
        out = render_coverage()
        target = RUNS_DIR / "coverage.md"
    else:
        out = render_scoreboard(suite=args.suite, lane=args.lane, limit=args.last)
        target = RUNS_DIR / "scoreboard.md"

    print(out)
    if args.write:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(out, encoding="utf-8")
        print(f"\n[scoreboard] wrote {target.relative_to(EVALS_DIR.parent)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
