"""Empty/unparseable judge verdicts must void as needs_rejudge — never auto-PASS."""

from __future__ import annotations

from evals.core import Case, CaseResult
from evals.judges import _judge_failure_diag, _needs_rejudge_verdict, _wrap
from evals.outcomes import VOID_HARNESS


def test_needs_rejudge_verdict_is_not_a_pass():
    # ~130k chars ≈ 32.5k tok + headroom exceeds default 32k num_ctx
    v = _needs_rejudge_verdict(
        user="x" * 130_000,
        kind="empty",
        detail="judge returned no parseable verdict: '(empty response)'",
        num_ctx=32768,
    )
    assert v["score"] is None
    assert v["pass"] is False
    assert v["needs_rejudge"] is True
    assert v["diag"]["likely_cause"] == "context_too_large"
    assert "user_chars=" in v["reasons"][1]


def test_diag_medium_prompt_fits_32k_ctx():
    """~52k chars ≈ 13k tok still fits 32k — do not stamp context_too_large."""
    diag = _judge_failure_diag(user="x" * 52_000, kind="empty", num_ctx=32768)
    assert diag["likely_cause"] == "empty_or_unparseable_output"
    assert diag["est_input_tokens"] == 13_000


def test_diag_small_empty_is_not_context_too_large():
    diag = _judge_failure_diag(user="short", kind="empty", num_ctx=32768)
    assert diag["likely_cause"] == "empty_or_unparseable_output"


def test_diag_timeout():
    diag = _judge_failure_diag(user="x" * 100, kind="timeout", num_ctx=8192)
    assert diag["likely_cause"] == "judge_timeout"


def test_wrap_preserves_needs_rejudge_and_diag():
    wrapped = _wrap("response_grounded", _needs_rejudge_verdict(
        user="hi", kind="unparseable", detail="no json", num_ctx=4096, snippet="{nope",
    ))
    assert wrapped["rubric"] == "response_grounded"
    assert wrapped["needs_rejudge"] is True
    assert wrapped["passed"] is False
    assert wrapped["diag"]["kind"] == "unparseable"


def test_case_with_needs_rejudge_is_void_harness_not_pass():
    r = CaseResult(
        case_id="c1", prompt="p", lane="workflow",
        passed=True,  # deterministic asserts ok
        judge_results=[{
            "rubric": "response_grounded",
            "passed": False,
            "score": None,
            "needs_rejudge": True,
            "diag": {"likely_cause": "context_too_large", "user_chars": 90000},
            "reasons": ["needs_rejudge: empty"],
        }],
    )
    assert r.judge_needs_rejudge is True
    assert r.overall_passed is False
    assert r.classify() == VOID_HARNESS
    assert r.void_reason == "judge_needs_rejudge"
    assert r.is_void
    assert not r.counts_toward_pass_rate


def test_intentional_skip_still_allows_pass():
    """score=None without needs_rejudge = artifact absent — not a void."""
    r = CaseResult(
        case_id="c2", prompt="p", lane="dry",
        passed=True,
        judge_results=[{
            "rubric": "planner_quality",
            "passed": True,
            "score": None,
            "reasons": ["skipped: no plan artifact (run with --live, Phase 1)"],
        }],
    )
    assert r.judge_needs_rejudge is False
    assert r.classify() == "pass"


def test_render_transcript_marks_needs_rejudge():
    from evals.core import render_transcript
    case = Case(id="c1", prompt="hello")
    r = CaseResult(
        case_id="c1", prompt="hello", lane="live",
        passed=True,
        judge_results=[{
            "rubric": "response_grounded",
            "passed": False,
            "score": None,
            "needs_rejudge": True,
            "diag": {"likely_cause": "context_too_large", "user_chars": 50000,
                     "est_input_tokens": 12500, "num_ctx": 32768},
            "reasons": ["needs_rejudge: empty", "diag: …"],
        }],
    )
    md = render_transcript(case, r)
    assert "NEEDS_REJUDGE" in md
    assert "likely_cause=`context_too_large`" in md
