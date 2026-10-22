"""
judge_calibration.py -- judge-vs-human agreement (issue #312).

evals/judges.py's PASS_THRESHOLD (6.0 on a 0–10 scale) and every rubric prompt were picked without ever being
checked against human judgment. Per 2026 agent-eval practice, judge trustworthiness itself needs
measuring (judges have known length/position/self-preference biases and are non-deterministic).

This lane loads evals/datasets/judge_golden.jsonl (hand-labeled real + constructed transcripts),
re-runs each case's designated rubric through the SAME judge function production evals use, and
reports agreement against the human label -- overall, per-rubric, and every disagreement with the
judge's own reasoning attached so it's fast to eyeball whether the judge or the label is wrong.

Free-ish: costs exactly one judge call per golden case (same as running --judge on a normal suite),
no server/LM Studio JIT-loading beyond whatever --judge-model already needs.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Deliberately NOT in evals/datasets/ — that directory is globbed by core.list_suites() for
# --all/--suite, and this file uses a different schema (rubric/human_label/artifacts, not
# prompt/expect) that load_suite() can't parse (the same class of mismatch as
# datasets/intent_router.jsonl, which has its own standalone consumer for the same reason).
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


@dataclass
class GoldenCase:
    id: str
    rubric: str
    human_pass: bool
    human_score: Optional[float]
    artifacts: Dict[str, Any]
    source: str = "real"
    why: str = ""


@dataclass
class CalibrationResult:
    case: GoldenCase
    judge_pass: Optional[bool]      # None if judge was inconclusive (score=None)
    judge_score: Optional[float]
    reasons: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def agrees(self) -> Optional[bool]:
        """None (excluded from agreement %) when the judge was inconclusive -- an inconclusive
        verdict isn't a disagreement, it's the judge declining to answer (see judges.py's
        documented inconclusive-is-not-fail design)."""
        if self.judge_pass is None or self.error:
            return None
        return self.judge_pass == self.case.human_pass


def load_golden(path: Optional[str] = None) -> List[GoldenCase]:
    p = Path(path) if path else (GOLDEN_DIR / "judge_golden.jsonl")
    cases: List[GoldenCase] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p.name}:{i} invalid JSON: {e}")
        label = d.get("human_label", {})
        cases.append(GoldenCase(
            id=d["id"], rubric=d["rubric"],
            human_pass=bool(label.get("pass")), human_score=label.get("score"),
            artifacts=d.get("artifacts", {}), source=d.get("source", "real"),
            why=d.get("why", ""),
        ))
    return cases


async def run_calibration(judge_model: str, golden_path: Optional[str] = None
                          ) -> List[CalibrationResult]:
    from evals import judges

    adapter = judges.build_judge_adapter(judge_model)
    cases = load_golden(golden_path)
    results: List[CalibrationResult] = []
    for i, gc in enumerate(cases, 1):
        fn = judges.JUDGES.get(gc.rubric)
        if fn is None:
            results.append(CalibrationResult(gc, None, None, error=f"unknown rubric '{gc.rubric}'"))
            continue
        try:
            verdict = await fn(adapter, **gc.artifacts)
        except Exception as e:
            results.append(CalibrationResult(gc, None, None,
                                             error=f"{type(e).__name__}: {e}"))
            continue
        results.append(CalibrationResult(
            gc,
            # score=None / needs_rejudge = judge declined — exclude from agreement (not a disagree).
            None if (verdict.get("score") is None or verdict.get("needs_rejudge"))
            else verdict.get("passed"),
            verdict.get("score"),
            verdict.get("reasons") or [],
        ))
        mark = (
            "?" if (verdict.get("score") is None or verdict.get("needs_rejudge"))
            else ("OK" if verdict.get("passed") == gc.human_pass else "DISAGREE")
        )
        print(f"  [{i}/{len(cases)}] {gc.id} ({gc.rubric}): human={gc.human_pass} "
              f"judge={verdict.get('passed')} score={verdict.get('score')} [{mark}]")
    return results


def render_report(judge_model: str, results: List[CalibrationResult]) -> str:
    scored = [r for r in results if r.agrees is not None]
    agree = sum(1 for r in scored if r.agrees)
    rate = round(agree / len(scored), 3) if scored else None
    inconclusive = sum(1 for r in results if r.judge_pass is None and not r.error)
    errored = sum(1 for r in results if r.error)

    L = [
        "# JUDGE CALIBRATION REPORT",
        f"Judge model : {judge_model}",
        f"Golden cases: {len(results)}  (scored={len(scored)}, inconclusive={inconclusive}, errors={errored})",
        f"Agreement   : {agree}/{len(scored)} ({rate:.0%})" if rate is not None else "Agreement   : n/a (no scored cases)",
        "",
    ]

    by_rubric: Dict[str, List[CalibrationResult]] = {}
    for r in results:
        by_rubric.setdefault(r.case.rubric, []).append(r)
    L.append("## Per-rubric agreement")
    for rubric, rs in sorted(by_rubric.items()):
        sc = [r for r in rs if r.agrees is not None]
        a = sum(1 for r in sc if r.agrees)
        pct = f"{a}/{len(sc)} ({a / len(sc):.0%})" if sc else "n/a"
        L.append(f"- **{rubric}**: {pct}")
    L.append("")

    disagreements = [r for r in results if r.agrees is False]
    L.append("## Disagreements (judge vs. human label)")
    if not disagreements:
        L.append("None — judge agreed with every scored human label.")
    for r in disagreements:
        L += [
            f"\n### {r.case.id} ({r.case.rubric}, source={r.case.source})",
            f"- Human : pass={r.case.human_pass} score={r.case.human_score}",
            f"- Judge : pass={r.judge_pass} score={r.judge_score}",
            f"- Human rationale: {r.case.why}",
            f"- Judge reasons: {'; '.join(r.reasons) or '(none given)'}",
        ]

    if inconclusive or errored:
        L.append("\n## Inconclusive / errored (excluded from agreement %)")
        for r in results:
            if r.error:
                L.append(f"- {r.case.id}: ERROR — {r.error}")
            elif r.judge_pass is None:
                L.append(f"- {r.case.id}: inconclusive — {'; '.join(r.reasons) or '(no reason)'}")

    return "\n".join(L)
