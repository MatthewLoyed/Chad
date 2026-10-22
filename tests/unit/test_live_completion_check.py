"""Observable-state completion criteria for plan-less lanes (#566 problem 2).

The bug: `code_agent` reads the target file and ends the turn with "Task completed."
having produced no diff. `evals/live.py`'s continue-nudge only fires when plan steps
remain, and agent-lane turns have no plan — so nothing ever pushed it to make the edit.

The fix follows the documented remedy for "premature completion": anchor the stop
condition to observable state, name the specific gap in the nudge, and bound the retries.
See docs/evals/PREMATURE_COMPLETION.md.
"""
import pytest

from evals.live import LiveTrace, _evaluate_completion_check


def _trace():
    t = LiveTrace(prompt="fix the bug", category="agent", rounds=1)
    t.rounds_detail = [{}]
    return t


def test_bool_predicate_satisfied():
    assert _evaluate_completion_check(lambda: True, _trace()) == (True, "")


def test_bool_predicate_unsatisfied_gets_a_default_gap_description():
    satisfied, gap = _evaluate_completion_check(lambda: False, _trace())
    assert satisfied is False
    assert gap  # never empty — the nudge interpolates it


def test_tuple_predicate_carries_the_callers_own_wording():
    satisfied, gap = _evaluate_completion_check(lambda: (False, "no file was modified"), _trace())
    assert (satisfied, gap) == (False, "no file was modified")


def test_a_raising_predicate_counts_as_satisfied_and_is_recorded():
    """A broken check must not manufacture a model failure — that is the opposite of the point."""
    trace = _trace()

    def _boom():
        raise RuntimeError("git status failed")

    satisfied, _ = _evaluate_completion_check(_boom, trace)

    assert satisfied is True
    assert "RuntimeError" in trace.rounds_detail[-1]["completion_check_error"]


def test_a_raising_predicate_with_no_rounds_recorded_does_not_explode():
    trace = LiveTrace(prompt="p", category="agent", rounds=0)
    trace.rounds_detail = []

    def _boom():
        raise RuntimeError("early")

    assert _evaluate_completion_check(_boom, trace) == (True, "")


def test_the_nudge_names_the_specific_gap():
    """Generic 'be thorough' text measurably does not move models; a named gap does."""
    from evals.live import COMPLETION_NUDGE

    text = COMPLETION_NUDGE.format(gap="no file has been modified yet")

    assert "no file has been modified yet" in text
    # It must push toward acting, not toward asking or narrating.
    assert "ask" in text.lower() and "describe" in text.lower()


def test_coder_bench_predicate_reflects_real_worktree_state(tmp_path):
    """The bench's predicate is `git status --porcelain` over its disposable checkout."""
    import subprocess

    from evals.coder_bench import _git

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "mod.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path, check=True,
    )

    def _has_edit():
        r = _git(["status", "--porcelain"], cwd=tmp_path)
        return (True, "") if r.stdout.strip() else (False, "no file has been modified")

    assert _has_edit()[0] is False, "a clean checkout must read as 'no work done'"

    (tmp_path / "mod.py").write_text("x = 2\n")
    assert _has_edit()[0] is True, "an edit must satisfy the predicate"


# --- scoring interaction (found by running the bench, not by unit tests) ----- #


def test_a_no_op_scores_as_no_op_not_backend_error():
    """`error` means the PROBE failed; the model doing nothing is a result, not that.

    coder_bench's score() maps any `result.error` straight to `backend-error`. The first
    version of this feature set `error` on a completion-check failure, so a clean
    "agent made no edit" run was reported as infrastructure trouble and the far more
    useful `no-op` / "empty diff" bucket never fired. Caught by an actual bench run.
    """
    from evals.coder_bench import CoderResult, CoderTask, score

    task = CoderTask(id="t", prompt="p", base_commit="abc123", issue_ref="#566",
                     fail_to_pass=[], pass_to_pass=[])
    trace = LiveTrace(prompt="p", category="agent", rounds=4)
    trace.completed = False
    trace.completion_gap = "no file in the repository has been modified yet"
    trace.continue_nudges = 3
    result = CoderResult(backend="inhouse", task_id="t", diff="", trace=trace, error=trace.error)

    verdict = score(task, result, worktree_dir=None)

    assert verdict.outcome == "no-op"
    assert "empty diff" in verdict.detail
    # ...and the effort spent before giving up must survive into the report.
    assert "3" in verdict.detail


def test_plan_diagnosis_is_not_emitted_for_a_plan_less_turn():
    """With no plan, 'plan unfinished (0/0 steps)' is nonsense — it must stay unset."""
    trace = LiveTrace(prompt="p", category="agent", rounds=1)
    trace.completed = False
    trace.completion_gap = "no diff"
    assert trace.error is None
