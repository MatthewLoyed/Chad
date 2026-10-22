r"""
gate.py - orchestration: run the checks a run needs, render the verdict.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from evals.preflight.capabilities import requirements_for_cases
from evals.preflight.checks import CHECKS, _ALWAYS
from evals.preflight.model import (FAIL, WARN, _STATUS_ICON, CheckResult,
                                       PreflightContext)

# -- Orchestration ------------------------------------------------------------


@dataclass
class PreflightReport:
    results: List[CheckResult]
    requirements: List[str]
    elapsed_s: float

    @property
    def blocking(self) -> List[CheckResult]:
        return [r for r in self.results if r.blocking]

    @property
    def warnings(self) -> List[CheckResult]:
        return [r for r in self.results if r.status == WARN]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def manifest(self) -> Dict[str, Any]:
        """Environment snapshot folded into the run record, so a result months from now
        can still be attributed to the environment that produced it."""
        return {
            "requirements": sorted(self.requirements),
            "elapsed_s": round(self.elapsed_s, 2),
            "checks": {r.name: {"status": r.status, "detail": r.detail, "facts": r.facts}
                       for r in self.results},
        }

    def render(self) -> str:
        # ASCII only: this prints to a cp1252 Windows console, where box-drawing
        # characters raise UnicodeEncodeError and would crash the gate itself.
        rule = "-" * 74
        lines = ["", f"--- PREFLIGHT {rule[:60]}"]
        for r in sorted(self.results, key=lambda r: (r.status != FAIL, r.status != WARN, r.name)):
            timing = f"  ({r.elapsed_s}s)" if r.elapsed_s >= 0.5 else ""
            lines.append(f"{_STATUS_ICON[r.status]} {r.name:<14} {r.detail}{timing}")
            if r.remedy and r.status in (FAIL, WARN):
                lines.append(f"       -> {r.remedy}")
        n_fail, n_warn = len(self.blocking), len(self.warnings)
        verdict = (f"BLOCKED - {n_fail} check(s) failed" if n_fail
                   else f"clear ({n_warn} warning(s))" if n_warn else "clear")
        lines += [f"--- {verdict} in {self.elapsed_s:.1f}s {rule[:40]}", ""]
        return "\n".join(lines)


# Rough per-case wall-clock by lane, used only to size the credential-horizon check.
# Deliberately conservative: over-estimating the run length makes the OAuth check
# stricter, which is the safe direction. Dry-run assumes the 9B selector stays
# resident (#632); divide by concurrency at estimate time.
_LANE_SECONDS_PER_CASE = {"dry-run": 10.0, "live": 90.0, "workflow": 240.0}


def estimate_run_seconds(n_cases: int, lanes: Iterable[str], repeat: int = 1,
                         concurrency: int = 1) -> float:
    per_case = max((_LANE_SECONDS_PER_CASE.get(l, 12.0) for l in lanes), default=12.0)
    workers = max(1, concurrency) if "dry-run" in set(lanes) and "live" not in set(lanes) and "workflow" not in set(lanes) else 1
    return (n_cases * max(1, repeat) * per_case) / workers


def run_preflight(ctx: PreflightContext, requirements: Optional[Iterable[str]] = None,
                  *, only: Optional[Iterable[str]] = None) -> PreflightReport:
    """Run the checks the run needs. Unknown requirement names are ignored, so adding a
    capability to the tool map before writing its check is harmless."""
    reqs = set(requirements) if requirements is not None else requirements_for_cases(
        ctx.cases, lanes=ctx.lanes)
    reqs |= _ALWAYS
    if ctx.judge_model:
        reqs.add("judge_model")
    if only:
        reqs = set(only)

    t0 = time.time()
    results: List[CheckResult] = []
    for name in sorted(reqs):
        fn = CHECKS.get(name)
        if not fn:
            continue
        started = time.time()
        try:
            res = fn(ctx)
        except Exception as e:
            # A check that crashes must not take the run down silently, but it also can't
            # be treated as a pass - we don't know the state it was meant to verify.
            res = CheckResult(name, FAIL, f"check itself crashed: {type(e).__name__}: {e}",
                              remedy="Fix the preflight check, or skip it with "
                                     "--skip-preflight if you accept the risk.")
        if not res.elapsed_s:
            res.elapsed_s = round(time.time() - started, 2)
        results.append(res)

    return PreflightReport(results=results, requirements=sorted(reqs),
                           elapsed_s=round(time.time() - t0, 2))



def main(argv: Optional[List[str]] = None) -> int:
    """Standalone entry: `python -m evals.preflight --suite smoke --live`."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Validate the eval environment before spending hours on a run.")
    ap.add_argument("--suite", "-s", action="append", default=[],
                    help="suite to derive requirements from (repeatable). Default: all.")
    ap.add_argument("--live", action="store_true", help="assume the --live lane")
    ap.add_argument("--workflow", action="store_true", help="assume the --workflow lane")
    ap.add_argument("--judge-model", default=None, help="judge model id to verify is served")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--check", action="append", default=[],
                    help="run only these checks (repeatable), ignoring inference")
    ap.add_argument("--json", action="store_true", help="emit the manifest as JSON")
    args = ap.parse_args(argv)

    from evals.core import list_suites, load_suite

    suites = args.suite or list_suites()
    cases: List[Any] = []
    load_errors: Dict[str, str] = {}
    for s in suites:
        try:
            cases.extend(load_suite(s))
        except Exception as e:
            load_errors[s] = f"{type(e).__name__}: {e}"

    lanes = ["workflow"] if args.workflow else ["live"] if args.live else ["dry-run"]
    ctx = PreflightContext(
        judge_model=args.judge_model, lanes=lanes, suites=list(suites), cases=cases,
        estimated_run_s=estimate_run_seconds(len(cases), lanes, args.repeat),
        load_errors=load_errors,
    )
    report = run_preflight(ctx, only=args.check or None)
    if args.json:
        print(json.dumps(report.manifest(), indent=2))
    else:
        print(report.render())
    return 0 if report.ok else 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
