"""Tests for the eval outcome taxonomy, advisory budgets and scoreboard math.

These guard the properties that make eval data trustworthy:

  * infrastructure failure is NEVER scored as model capability failure;
  * a shape metric (plan steps, duration, rounds) never fails a case unless the dataset
    explicitly opted in;
  * a run that voided most of its cases is flagged as not comparable;
  * regression/flakiness detection doesn't read void outcomes as failures.

If any of these break, every eval number downstream becomes misleading — which is the
specific failure mode this suite exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from evals.core import Case, CaseResult, aggregate_runs
from evals.outcomes import (FAIL, PASS, VOID_HARNESS, VOID_INFRA, check_budgets,
                            classify_error, summarize, tool_selection_metrics)


# ── classify_error ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("error,expected_outcome,expected_reason", [
    ("invalid_grant: Token has been expired or revoked", VOID_INFRA, "google_oauth_expired"),
    ("no auth token (set CHAD_REMOTE_TOKEN)", VOID_INFRA, "auth"),
    ("auth rejected: {'status': 'failed'}", VOID_INFRA, "auth"),
    ("server not running", VOID_INFRA, "server_down"),
    ("URLError: <urlopen error [WinError 10061]>", VOID_INFRA, "network"),
    ("ConnectionClosed: no close frame received", VOID_INFRA, "network"),
    ("model_not_found: qwen/qwen3.5-9b", VOID_INFRA, "model_lifecycle"),
    ("Engine protocol startup was aborted", VOID_INFRA, "model_lifecycle"),
    ("HTTP 429 rate limit exceeded", VOID_INFRA, "rate_limit"),
])
def test_infra_errors_are_voided_not_failed(error, expected_outcome, expected_reason):
    outcome, reason = classify_error(error)
    assert outcome == expected_outcome
    assert reason == expected_reason


def test_harness_errors_are_voided_as_harness():
    outcome, reason = classify_error("workflows.jsonl:16 unknown expect key(s) ['tools_all']")
    assert outcome == VOID_HARNESS
    assert reason == "bad_case"


def test_no_error_is_a_pass():
    assert classify_error(None) == (PASS, "")
    assert classify_error("") == (PASS, "")


def test_unrecognized_error_is_voided_not_silently_failed():
    """An unknown error must not be scored as a model failure — unknown means unknown."""
    outcome, reason = classify_error("something nobody has ever seen before")
    assert outcome == VOID_HARNESS
    assert reason == "unclassified_error"


def test_timeout_before_any_work_is_infra_but_after_work_is_a_real_failure():
    """The one genuinely ambiguous error. A turn that never started is the environment's
    fault; a turn that ran tools and then spiralled is the model's, and voiding it would
    hide exactly the pathology we hunt for."""
    before = classify_error("no final response within 300s", produced_work=False)
    assert before == (VOID_INFRA, "turn_timeout_before_any_work")

    after = classify_error("no final response within 300s", produced_work=True)
    assert after[0] == FAIL
    assert after[1] == "turn_timeout_after_work"

    # Server-emitted step timeouts (NodeTimeoutError from conversation_orchestrator / supervisor)
    server_timeout = (
        "⚠️ This step exceeded the maximum allowed time (900s). Try breaking the request into smaller tasks."
    )
    assert classify_error(server_timeout, produced_work=False) == (VOID_INFRA, "turn_timeout_before_any_work")
    assert classify_error(server_timeout, produced_work=True) == (FAIL, "turn_timeout_after_work")

    server_idle = (
        "⚠️ This step took too long without a response (over 600s idle). Try breaking the request into smaller tasks, or retry."
    )
    assert classify_error(server_idle, produced_work=False) == (VOID_INFRA, "turn_timeout_before_any_work")
    assert classify_error(server_idle, produced_work=True) == (FAIL, "turn_timeout_after_work")


# ── CaseResult.classify ──────────────────────────────────────────────────────


def test_case_result_uses_metrics_to_decide_timeout_attribution():
    did_nothing = CaseResult("c1", "p", passed=False,
                             error="no final response within 300s",
                             metrics={"tool_count": 0, "response_chars": 0})
    assert did_nothing.outcome == VOID_INFRA
    assert did_nothing.is_void
    assert not did_nothing.counts_toward_pass_rate

    did_work = CaseResult("c2", "p", passed=False,
                          error="no final response within 300s",
                          metrics={"tool_count": 4, "response_chars": 0})
    assert did_work.outcome == FAIL
    assert did_work.counts_toward_pass_rate


def test_assertion_failure_without_error_is_a_real_fail():
    r = CaseResult("c", "p", passed=False, assertion_failures=["tools_include: missing ['x']"])
    assert r.outcome == FAIL
    assert r.counts_toward_pass_rate


def test_clean_pass():
    r = CaseResult("c", "p", passed=True)
    assert r.outcome == PASS
    assert not r.is_void


# ── summarize ────────────────────────────────────────────────────────────────


def test_void_cases_are_excluded_from_the_pass_rate_denominator():
    """The headline property. 2 passed, 1 real fail, 2 voided by a dead token must report
    2/3 = 0.667 — NOT 2/5 = 0.4, which would read as a model regression."""
    results = [
        CaseResult("ok1", "p", passed=True),
        CaseResult("ok2", "p", passed=True),
        CaseResult("bad", "p", passed=False, assertion_failures=["wrong tool"]),
        CaseResult("v1", "p", passed=False, error="invalid_grant: Token has been expired"),
        CaseResult("v2", "p", passed=False, error="invalid_grant: Token has been expired"),
    ]
    s = summarize(results)
    assert s["total"] == 5
    assert s["scored"] == 3
    assert s["passed"] == 2
    assert s["failed"] == 1
    assert s["void"] == 2
    assert s["pass_rate"] == pytest.approx(0.667, abs=0.001)


def test_void_reasons_are_clustered_for_one_fix_not_n_investigations():
    results = [CaseResult(f"c{i}", "p", passed=False,
                          error="invalid_grant: Token has been expired") for i in range(3)]
    results.append(CaseResult("net", "p", passed=False, error="URLError: refused"))
    s = summarize(results)
    assert s["void_by_reason"]["google_oauth_expired"] == ["c0", "c1", "c2"]
    assert s["void_by_reason"]["network"] == ["net"]


def test_high_void_rate_marks_the_run_untrustworthy():
    clean = [CaseResult(f"c{i}", "p", passed=True) for i in range(10)]
    assert summarize(clean)["trustworthy"] is True

    # 2 of 11 voided = 18% > the 10% threshold.
    dirty = clean + [CaseResult("v1", "p", passed=False, error="server not running"),
                     CaseResult("v2", "p", passed=False, error="server not running")]
    s = summarize(dirty)
    assert s["trustworthy"] is False
    assert s["pass_rate"] == 1.0, "the cases that DID run all passed; the rate reflects only those"


def test_all_void_run_has_no_pass_rate_rather_than_zero():
    """A run where nothing reached a healthy server must not report 0% — that reads as
    total model failure when in fact nothing was measured."""
    s = summarize([CaseResult("a", "p", passed=False, error="server not running"),
                   CaseResult("b", "p", passed=False, error="server not running")])
    assert s["pass_rate"] is None
    assert s["trustworthy"] is False


# ── advisory budgets ─────────────────────────────────────────────────────────


def test_budget_breach_is_reported_but_does_not_fail_the_case():
    breaches = check_budgets({"min_steps": 3, "max_duration_s": 30},
                             {"plan_steps": 1, "duration_s": 95.0})
    assert len(breaches) == 2
    assert any("min_steps" in b for b in breaches)
    assert any("max_duration_s" in b for b in breaches)


def test_budget_ignores_metrics_the_lane_did_not_produce():
    """A dry-run lane has no duration/rounds. A missing metric must be silent, not a breach
    — an absent measurement is not a violated one."""
    assert check_budgets({"max_duration_s": 10, "max_rounds": 2}, {"tool_count": 3}) == []


def test_empty_budget_is_noop():
    assert check_budgets({}, {"duration_s": 9999}) == []


def test_advisory_keys_in_expect_are_migrated_into_budget(tmp_path):
    """Shape metrics written the old way (inside `expect`) must stop gating automatically,
    so no existing dataset silently keeps failing correct-but-differently-shaped runs."""
    from evals.core import load_suite

    p = tmp_path / "migrate.jsonl"
    p.write_text(json.dumps({
        "id": "m1", "prompt": "do a thing",
        "expect": {"tools_include": ["web_search"], "min_steps": 3, "max_duration_s": 60},
    }) + "\n", encoding="utf-8")

    case = load_suite(str(p))[0]
    assert case.expect == {"tools_include": ["web_search"]}, "advisory keys must leave expect"
    assert case.budget == {"min_steps": 3, "max_duration_s": 60}


def test_budget_can_opt_into_gating_explicitly(tmp_path):
    from evals.core import load_suite
    from evals.outcomes import budget_is_hard

    p = tmp_path / "hard.jsonl"
    p.write_text(json.dumps({
        "id": "h1", "prompt": "be fast",
        "budget": {"max_duration_s": 5, "hard": True},
    }) + "\n", encoding="utf-8")

    case = load_suite(str(p))[0]
    assert budget_is_hard(case.budget) is True


def test_unknown_budget_key_is_rejected_not_ignored(tmp_path):
    """A typo'd budget key that silently no-ops means a limit nobody is enforcing."""
    from evals.core import load_suite

    p = tmp_path / "typo.jsonl"
    p.write_text(json.dumps({"id": "t", "prompt": "x",
                             "budget": {"max_duration_sec": 5}}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown budget key"):
        load_suite(str(p))


def test_suite_without_prompt_is_rejected_with_a_pointer_to_probe_datasets(tmp_path):
    from evals.core import load_suite

    p = tmp_path / "probe.jsonl"
    p.write_text(json.dumps({"id": "q", "query": "hi", "expect_mode": "conversation"}) + "\n",
                 encoding="utf-8")
    with pytest.raises(ValueError, match="not a runner suite"):
        load_suite(str(p))


# ── tool-selection metrics ───────────────────────────────────────────────────


def test_tool_selection_precision_and_recall():
    m = tool_selection_metrics(["web_search", "read_file"], ["web_search", "set_volume"])
    assert m["tool_recall"] == 0.5
    assert m["tool_precision"] == 0.5
    assert m["tool_missing"] == ["read_file"]
    assert m["tool_extra"] == ["set_volume"]


def test_tool_selection_distinguishes_precise_from_noisy_success():
    """Both runs called the right tool. Only precision tells them apart, which is why a
    boolean 'did it include the tool' is not enough signal."""
    precise = tool_selection_metrics(["web_search"], ["web_search"])
    noisy = tool_selection_metrics(["web_search"], ["web_search", "a", "b", "c"])
    assert precise["tool_recall"] == noisy["tool_recall"] == 1.0
    assert precise["tool_precision"] == 1.0
    assert noisy["tool_precision"] == 0.25


def test_tool_selection_metrics_empty():
    assert tool_selection_metrics([], []) == {}


def test_tool_selection_metrics_undeclared_expectation_reports_nothing():
    """Regression: a case with no `tools_include` has nothing to score precision/recall
    against. Reporting `tool_precision: 0.0` anyway (the old behavior) misread 12 correct,
    undeclared tool calls as '12 wrong tools' on a real live eval run."""
    assert tool_selection_metrics([], ["web_search", "google_drive_search", "read_file"]) == {}


# ── multi-run aggregation ────────────────────────────────────────────────────


def test_void_runs_are_dropped_from_pass_hat_k():
    """A case that passed twice and was voided once must not be marked a pass^k failure —
    the void was infrastructure, not a model miss."""
    case = Case(id="c", prompt="p")
    runs = [CaseResult("c", "p", passed=True),
            CaseResult("c", "p", passed=True),
            CaseResult("c", "p", passed=False, error="server not running")]
    agg = aggregate_runs(case, runs)
    assert agg.repeat["n_scored"] == 2
    assert agg.repeat["n_void"] == 1
    assert agg.repeat["pass_hat_k"] is True
    assert agg.overall_passed is True


def test_all_runs_void_yields_no_verdict():
    case = Case(id="c", prompt="p")
    runs = [CaseResult("c", "p", passed=False, error="server not running") for _ in range(3)]
    agg = aggregate_runs(case, runs)
    assert agg.repeat["all_void"] is True
    assert agg.repeat["pass_rate"] is None
    assert agg.is_void


def test_genuine_flakiness_still_fails_pass_hat_k():
    case = Case(id="c", prompt="p")
    runs = [CaseResult("c", "p", passed=True),
            CaseResult("c", "p", passed=False, assertion_failures=["wrong tool"]),
            CaseResult("c", "p", passed=True)]
    agg = aggregate_runs(case, runs)
    assert agg.repeat["pass_at_k"] is True
    assert agg.repeat["pass_hat_k"] is False


# ── scoreboard history analysis ──────────────────────────────────────────────


def _history(outcomes):
    from evals.scoreboard import CaseHistory
    return CaseHistory("c", outcomes=list(outcomes), timestamps=[""] * len(outcomes))


def test_regression_requires_a_stable_green_streak_first():
    from evals.scoreboard import _REGRESSION_GREEN_STREAK
    assert _REGRESSION_GREEN_STREAK == 2

    regressed = _history(["pass", "pass", "pass", "fail"])
    assert regressed.is_regression is True

    # One pass then a fail is noise, not a regression.
    noisy = _history(["fail", "pass", "fail"])
    assert noisy.is_regression is False


def test_void_outcomes_do_not_count_as_failures_in_history():
    """A green case voided by a dead token must not be reported as a regression."""
    h = _history(["pass", "pass", "pass", "void_infra"])
    assert h.latest == "pass", "void is not a verdict; the last real verdict stands"
    assert h.is_regression is False
    assert h.void_rate == 0.25


def test_flaky_case_is_not_reported_as_a_regression():
    h = _history(["pass", "fail", "pass", "fail", "pass", "fail"])
    assert h.is_flaky is True
    assert h.is_regression is False, "a flaky case failing again is not new information"


def test_saturated_case_is_flagged_for_graduation():
    from evals.scoreboard import _SATURATED_STREAK
    h = _history(["pass"] * _SATURATED_STREAK)
    assert h.is_saturated is True
    assert h.green_streak == _SATURATED_STREAK


def test_never_passed_case_is_flagged_as_probably_a_broken_spec():
    h = _history(["fail", "fail", "fail"])
    assert h.never_passed is True


def test_untrustworthy_runs_are_excluded_from_the_trend(tmp_path, monkeypatch):
    """A run that voided most cases must not become a data point — plotting it would show a
    phantom cliff or a phantom recovery."""
    import evals.scoreboard as sb

    hist = tmp_path / "history.jsonl"
    records = [
        {"timestamp": "1", "suite": "s", "lane": "live",
         "summary": {"pass_rate": 0.80, "trustworthy": True}, "cases": {}},
        {"timestamp": "2", "suite": "s", "lane": "live",
         "summary": {"pass_rate": 0.10, "trustworthy": False}, "cases": {}},
        {"timestamp": "3", "suite": "s", "lane": "live",
         "summary": {"pass_rate": 0.85, "trustworthy": True}, "cases": {}},
    ]
    hist.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    monkeypatch.setattr(sb, "HISTORY_PATH", hist)

    t = sb.trend("s", lane="live")
    assert [p[1] for p in t.points] == [0.80, 0.85]
    assert t.excluded_untrustworthy == 1
    assert t.delta == pytest.approx(0.05)


def test_history_roundtrip_and_case_outcomes(tmp_path, monkeypatch):
    import evals.scoreboard as sb

    monkeypatch.setattr(sb, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(sb, "RUNS_DIR", tmp_path)

    sb.append_history({
        "timestamp": "20260724_120000", "suite": "smoke", "lane": "live", "judged": True,
        "summary": {"total": 2, "scored": 1, "passed": 1, "failed": 0, "void": 1,
                    "pass_rate": 1.0, "void_rate": 0.5, "void_by_reason": {"auth": ["b"]},
                    "trustworthy": False, "flaky": []},
        "manifest": {"git_sha": "abc1234", "judge_model": "qwen/qwen3.5-9b"},
        "cases": [{"id": "a", "outcome": "pass"}, {"id": "b", "outcome": "void_infra"}],
    })

    loaded = sb.load_history(suite="smoke")
    assert len(loaded) == 1
    assert loaded[0]["cases"] == {"a": "pass", "b": "void_infra"}
    assert loaded[0]["manifest"]["git_sha"] == "abc1234"

    hists = sb.case_histories(suite="smoke")
    assert hists["a"].latest == "pass"
    assert hists["b"].latest is None, "a void-only case has no verdict at all"


def test_comparability_key_separates_judge_models():
    from evals.scoreboard import comparability_key
    a = comparability_key({"lane": "live", "judged": True, "judge_model": "gemma"}, "smoke")
    b = comparability_key({"lane": "live", "judged": True, "judge_model": "qwen"}, "smoke")
    assert a != b, "results judged by different models are not comparable"
