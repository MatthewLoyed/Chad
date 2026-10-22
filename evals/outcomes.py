r"""
outcomes.py - the outcome taxonomy, advisory budgets, and run summary math.

Split out of core.py, which had grown past this repo's module ceiling while owning four
unrelated jobs (dataset loading, assertions, reporting, transcripts). This module owns
exactly one question: **given what a case produced, what does it MEAN?**

It deliberately has no dependency on `Case`/`CaseResult`; every function here is duck-typed
over the attributes it reads. That keeps the dependency one-way (core.py -> outcomes.py) and
makes this logic testable without constructing a whole harness result.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:          # import only for type checkers — keeps the dependency one-way
    from evals.core import CaseResult

# ── Hard correctness vs advisory budgets ─────────────────────────────────────
#
# Some of the keys above measure SHAPE, not correctness: how many plan steps the planner
# emitted, how long the turn took, how many approve/execute rounds it needed. Anthropic's
# agent-eval guidance is explicit that grading the path rather than the outcome produces
# "overly brittle tests" — agents find valid approaches you didn't anticipate — and that a
# timeout caused by infrastructure must never be scored as a capability failure.
#
# We learned both the hard way: `min_steps` failed runs where the model did the right thing
# in one step, and `max_duration_s` failed runs where LM Studio was mid-model-swap.
#
# These keys are therefore ADVISORY by default: always measured, recorded, and trended, but
# not gating. A case that genuinely needs one to gate can restate it under a `budget` block
# with `"hard": true`, which is an explicit, reviewable choice rather than a silent default.
_ADVISORY_EXPECT_KEYS = {
    "min_steps", "max_steps", "max_duration_s", "max_rounds", "min_rounds",
    "min_tools", "min_tool_count", "min_steps_completed",
}

# Keys legal inside a `budget` block. Same names as their `expect` counterparts, plus the
# `hard` opt-in escape hatch.
_VALID_BUDGET_KEYS = _ADVISORY_EXPECT_KEYS | {
    "max_system_tokens", "max_total_tokens", "max_tools", "hard",
}


# ── Outcome taxonomy ─────────────────────────────────────────────────────────
#
# The single most expensive eval defect in this repo's history was not a wrong assertion —
# it was attributing INFRASTRUCTURE failure to model capability. A dead Google token (#476)
# or an empty search backend turns every dependent case red, the pass rate craters, and the
# run reads as "the model regressed". One #413 sweep was contaminated exactly this way, and
# 27 of 28 "the 14B can't do workflows" failures were a dead sandbox.
#
# So a case has FOUR outcomes, not two. Only `pass`/`fail` are model signal; VOID cases are
# excluded from the pass-rate denominator entirely and reported in their own section, because
# a void case tells you nothing about the model and averaging it in actively lies to you.
PASS = "pass"
FAIL = "fail"
VOID_INFRA = "void_infra"        # the environment broke; the model was never fairly tested
VOID_HARNESS = "void_harness"    # the eval itself broke (bad case, harness bug)

VOID_OUTCOMES = (VOID_INFRA, VOID_HARNESS)

# Error-string signatures -> (outcome, reason). Ordered: first match wins, so put the
# specific patterns before the generic ones.
_ERROR_SIGNATURES: List[tuple] = [
    # Credentials / auth — never the model's fault.
    (r"invalid_grant|Token has been expired|token has been revoked", VOID_INFRA, "google_oauth_expired"),
    (r"no auth token|auth rejected|401|403|Unauthorized", VOID_INFRA, "auth"),
    # Model lifecycle (LM Studio JIT/Auto-Evict, issue #592).
    (r"model_not_found|Model unloaded|Failed to load model|Engine protocol startup|"
     r"model is not loaded", VOID_INFRA, "model_lifecycle"),
    # Transport — the request never reached a healthy server.
    (r"server not running|server not up|health check failed", VOID_INFRA, "server_down"),
    (r"ConnectionRefused|ConnectionResetError|ConnectionError|URLError|WinError 10061|"
     r"ClientConnector|Cannot connect to host|Server disconnected|"
     r"ConnectionClosed|WebSocketException|InvalidStatus", VOID_INFRA, "network"),
    # Upstream capacity.
    (r"429|rate.?limit|quota exceeded|insufficient_quota", VOID_INFRA, "rate_limit"),
    # Named external dependencies.
    (r"E2B|sandbox (?:dead|unavailable|timeout)", VOID_INFRA, "sandbox"),
    (r"docker|container", VOID_INFRA, "docker"),
    # Harness-side problems: the case could not have passed regardless of the model.
    (r"unknown expect key|unknown judge rubric|invalid JSON|FileNotFoundError|"
     r"fixture .*missing|KeyError", VOID_HARNESS, "bad_case"),
    (r"judge (?:call )?failed|judge error|judge timed out", VOID_HARNESS, "judge_unavailable"),
]


def classify_error(error: Optional[str], *, produced_work: bool = False) -> tuple:
    """Map a case-level error string to (outcome, reason).

    `produced_work` disambiguates the genuinely ambiguous case — a turn timeout. A turn
    that timed out having executed NOTHING never really started (model-load queue, gate
    serialization), which is infrastructure. A turn that called tools, streamed tokens and
    *then* ran out of time is a real failure (a reasoning spiral, a repeat-call loop) and
    must stay in the denominator — otherwise we'd void away exactly the pathologies we're
    hunting.
    """
    if not error:
        return PASS, ""
    for pattern, outcome, reason in _ERROR_SIGNATURES:
        if re.search(pattern, error, re.IGNORECASE):
            return outcome, reason
    if re.search(
        r"no final response within|TimeoutError|timed out|exceeded the maximum allowed time|took too long without a response",
        error,
        re.IGNORECASE,
    ):
        if produced_work:
            return FAIL, "turn_timeout_after_work"
        return VOID_INFRA, "turn_timeout_before_any_work"
    # An unrecognized error is NOT quietly scored as a model failure. Unknown means
    # unknown; it gets voided and surfaced so the signature list can grow.
    return VOID_HARNESS, "unclassified_error"


def check_budgets(budget: Dict[str, Any], metrics: Dict[str, Any]) -> List[str]:
    """Evaluate advisory budgets. Returns human-readable breach descriptions.

    These are recorded and trended but do NOT fail the case unless the budget block opts in
    with {"hard": true} — the caller decides what to do with the return value.
    """
    breaches: List[str] = []
    if not budget:
        return breaches

    def _m(*keys):
        for k in keys:
            if isinstance(metrics.get(k), (int, float)):
                return metrics[k]
        return None

    checks = (
        ("min_steps",          _m("plan_steps"),      lambda v, lim: v < lim,  "plan has {v} steps, budget {lim} (planner decomposition)"),
        ("max_steps",          _m("plan_steps"),      lambda v, lim: v > lim,  "plan has {v} steps, budget {lim}"),
        ("max_duration_s",     _m("duration_s"),      lambda v, lim: v > lim,  "took {v}s, budget {lim}s"),
        ("max_rounds",         _m("rounds"),          lambda v, lim: v > lim,  "{v} rounds, budget {lim}"),
        ("min_rounds",         _m("rounds"),          lambda v, lim: v < lim,  "{v} rounds, budget {lim}"),
        ("min_tools",          _m("tool_count"),      lambda v, lim: v < lim,  "{v} tools, budget {lim}"),
        ("min_tool_count",     _m("tool_count"),      lambda v, lim: v < lim,  "{v} tools, budget {lim}"),
        ("max_tools",          _m("tool_count"),      lambda v, lim: v > lim,  "{v} tools, budget {lim}"),
        ("min_steps_completed", _m("steps_completed"), lambda v, lim: v < lim, "{v} steps completed, budget {lim}"),
        ("max_system_tokens",  _m("system_tokens"),   lambda v, lim: v > lim,  "{v} system tokens, budget {lim}"),
        ("max_total_tokens",   _m("total_tokens"),    lambda v, lim: v > lim,  "{v} total tokens, budget {lim}"),
    )
    for key, actual, breached, tmpl in checks:
        if key not in budget or actual is None:
            continue
        limit = budget[key]
        if breached(actual, limit):
            breaches.append(f"{key}: " + tmpl.format(v=actual, lim=limit))
    return breaches


def budget_is_hard(budget: Dict[str, Any]) -> bool:
    """Did this case explicitly opt into its budgets gating the result?"""
    return bool(budget.get("hard"))


# ── Tool-selection metrics ───────────────────────────────────────────────────


def tool_selection_metrics(expected: List[str], actual: List[str]) -> Dict[str, Any]:
    """Precision / recall / F1 over tool selection, plus the concrete diff.

    A boolean "did it include the right tool" hides the difference between a model that
    called exactly the right tool and one that called it alongside six wrong ones. Both
    matter: recall tracks capability, precision tracks context bloat and side-effect risk.

    Requires a non-empty `expected` — with no declared expectation there is nothing to score
    precision/recall against, and reporting one anyway produces a misleading "tool_precision:
    0.0" that reads as "N wrong tool calls" for a turn that was never actually asserted on
    (caught live: every one of 12 correct, undeclared tool calls showed up as "extra").
    """
    exp, act = set(expected or []), set(actual or [])
    if not exp:
        return {}
    hit = exp & act
    precision = len(hit) / len(act) if act else 0.0
    recall = len(hit) / len(exp) if exp else None
    f1 = None
    if recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    out: Dict[str, Any] = {
        "tool_precision": round(precision, 3),
        "tool_extra": sorted(act - exp),
    }
    if recall is not None:
        out["tool_recall"] = round(recall, 3)
        out["tool_missing"] = sorted(exp - act)
    if f1 is not None:
        out["tool_f1"] = round(f1, 3)
    return out


def summarize(results: List["CaseResult"]) -> Dict[str, Any]:
    """The honest run summary.

    `pass_rate` is over SCORED cases only (void excluded). `void` is reported separately and
    prominently, because a run with many voids has a small effective sample size and its
    pass rate should not be compared against a clean run's.
    """
    total = len(results)
    scored = [r for r in results if r.counts_toward_pass_rate]
    passed = sum(1 for r in scored if r.outcome == PASS)
    voids = [r for r in results if r.is_void]

    void_by_reason: Dict[str, List[str]] = {}
    for r in voids:
        void_by_reason.setdefault(r.void_reason or "unknown", []).append(r.case_id)

    flaky = [r.case_id for r in results
             if r.repeat and not r.repeat.get("pass_hat_k") and r.repeat.get("pass_at_k")]

    return {
        "total": total,
        "scored": len(scored),
        "passed": passed,
        "failed": len(scored) - passed,
        "void": len(voids),
        "pass_rate": round(passed / len(scored), 3) if scored else None,
        "void_rate": round(len(voids) / total, 3) if total else 0.0,
        "void_by_reason": {k: sorted(v) for k, v in sorted(void_by_reason.items())},
        "budget_warnings": sum(len(r.budget_warnings) for r in results),
        "flaky": sorted(flaky),
        # A run that voided a lot of cases is not comparable to a clean one. Callers use
        # this to refuse to write a trend point rather than plotting a misleading number.
        "trustworthy": bool(scored) and (len(voids) / total if total else 0) <= 0.10,
    }

