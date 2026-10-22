"""The live non-progress guard (src/core/tools/repetition.py).

Only an identical call that ALREADY FAILED may be blocked. Everything else is
advisory — a wrong block destroys real work, a wrong hint costs one line.
"""

import pytest

from src.core.tools import repetition


@pytest.fixture(autouse=True)
def _fresh_guard():
    repetition.reset()
    yield
    repetition.reset()


def _guard():
    return repetition.RepetitionGuard()


# ── blocking ─────────────────────────────────────────────────────────────────


def test_identical_failing_call_is_blocked_on_repeat():
    g = _guard()
    args = {"query": "venues"}
    assert g.check("web_search", args) is None
    g.note("web_search", args, success=False)

    blocked = g.check("web_search", args)
    assert blocked is not None
    assert blocked["failure_class"] == "blocked"
    assert blocked["retryable"] is False
    assert "NEXT_ACTIONS" in blocked["error"]


def test_different_arguments_are_not_blocked():
    """Changing the call IS the correct recovery — never punish it."""
    g = _guard()
    g.note("web_search", {"query": "venues"}, success=False)
    assert g.check("web_search", {"query": "wedding venues near me"}) is None


def test_redundant_successful_calls_blocked_at_ceiling():
    """Identical calls that succeed 1-2x get a hint; at the ceiling (3x), they are blocked."""
    g = _guard()
    args = {"path": "notes.txt"}
    for _ in range(2):
        g.note("read_file", args, success=True)
        assert g.check("read_file", args) is None

    g.note("read_file", args, success=True)  # 3rd success
    blocked = g.check("read_file", args)
    assert blocked is not None
    assert blocked["failure_class"] == "blocked"
    assert "already succeeded 3 time(s)" in blocked["error"]


def test_internal_args_do_not_change_call_identity():
    """`tool_registry` and `_approval_token` are plumbing injected by the harness;
    if they counted, no two calls would ever match."""
    g = _guard()
    g.note("web_search", {"query": "x", "tool_registry": object()}, success=False)
    assert g.check("web_search", {"query": "x", "_approval_token": "abc"}) is not None


def test_domain_guarded_families_are_left_to_their_own_blockers():
    """browser_* runs vision-assist and the sandbox latches auth across the whole
    family — pre-empting them here would lose that behaviour."""
    g = _guard()
    for tool in ("browser_click", "run_sandboxed_bash"):
        g.note(tool, {"target": "e1"}, success=False)
        g.note(tool, {"target": "e1"}, success=False)
        assert g.check(tool, {"target": "e1"}) is None


# ── hints ────────────────────────────────────────────────────────────────────


def test_repeating_a_successful_call_gets_a_reuse_hint():
    g = _guard()
    args = {"path": "notes.txt"}
    assert g.note("read_file", args, success=True) is None
    hint = g.note("read_file", args, success=True)
    assert hint and "already in this conversation" in hint


def test_cycling_between_two_tools_gets_a_hint():
    g = _guard()
    hint = None
    for i in range(6):
        tool = "web_search" if i % 2 == 0 else "fetch_url"
        hint = g.note(tool, {"n": i}, success=True) or hint
    assert hint is not None
    assert "cycling" in hint.lower()


def test_ordinary_varied_work_produces_no_hint():
    g = _guard()
    hints = [
        g.note(tool, {"n": 1}, success=True)
        for tool in ("web_search", "read_file", "write_file", "get_datetime")
    ]
    assert not any(hints)


# ── turn scoping ─────────────────────────────────────────────────────────────


def test_guard_is_rebuilt_when_the_turn_changes():
    """Regression risk copied from the executor's counters, which are process-
    lifetime: a call that failed once must not stay blocked forever, in every
    later conversation."""
    from src.core.orchestration.context import turn_id_var

    token = turn_id_var.set("turn-1")
    try:
        g1 = repetition.current_guard()
        g1.note("web_search", {"q": "x"}, success=False)
        assert g1.check("web_search", {"q": "x"}) is not None
    finally:
        turn_id_var.reset(token)

    token = turn_id_var.set("turn-2")
    try:
        g2 = repetition.current_guard()
        assert g2 is not g1
        assert g2.check("web_search", {"q": "x"}) is None
    finally:
        turn_id_var.reset(token)


def test_same_turn_reuses_one_guard():
    from src.core.orchestration.context import turn_id_var

    token = turn_id_var.set("turn-stable")
    try:
        assert repetition.current_guard() is repetition.current_guard()
    finally:
        turn_id_var.reset(token)
