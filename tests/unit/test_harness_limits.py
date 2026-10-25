"""The harness-boundary registry (src/core/tools/limits.py).

The point of this module is that boundaries are discoverable and consistent, so
these tests guard those two properties rather than specific numbers.
"""

import pytest

from src.core.tools import limits


def test_every_limit_is_reachable_and_typed():
    for name, lim in limits.LIMITS.items():
        value = limits.get(name)
        assert isinstance(value, (int, float)), f"{name} returned {type(value)}"
        assert lim.kind in (limits.HINT, limits.CEILING)
        assert lim.what.strip(), f"{name} has no description"
        assert lim.key.count(".") >= 1, f"{name} key '{lim.key}' is not a dot-path"


def test_unknown_limit_raises_rather_than_silently_defaulting():
    """A typo must not read as 'boundary of 0' — that would disable a guard silently."""
    with pytest.raises(KeyError):
        limits.get("supervisor_turns_maxx")


def test_describe_all_covers_the_registry():
    assert {r["name"] for r in limits.describe_all()} == set(limits.LIMITS)


def test_render_table_lists_every_boundary():
    table = limits.render_table()
    for name in limits.LIMITS:
        assert name in table


def test_soft_thresholds_sit_below_their_ceilings():
    """A hint that fires at or after its ceiling can never actually warn anyone."""
    pairs = [
        ("supervisor_turns_soft", "supervisor_turns_max"),
        ("step_turns_soft", "step_turns_max"),
        ("delegate_failures_soft", "delegate_failures_max"),
    ]
    for soft, hard in pairs:
        assert limits.get(soft) < limits.get(hard), f"{soft} must be below {hard}"


def test_step_budget_fits_inside_the_turn_ceiling():
    """Regression: the old hardcoded 25-turn cap was unreachable alongside a
    20-turn-per-step budget, so a two-step plan could never finish — it died
    mid-step with no message and looked like the model giving up."""
    assert limits.get("supervisor_turns_max") >= 2 * limits.get("step_turns_max")


def test_failed_output_budget_is_tighter_than_successful_output():
    assert limits.get("failed_output_max_chars") < limits.get("tool_output_max_chars")


def test_tool_timeout_default_is_realistic_for_local_inference():
    """60s was a cloud-API number and cut off work that was about to succeed."""
    assert limits.get("tool_timeout_default_s") >= 120


def test_ceilings_are_the_exception():
    """Hints should outnumber hard stops; a harness that mostly stops the model
    is a harness that mostly destroys work."""
    ceilings = [l for l in limits.LIMITS.values() if l.kind == limits.CEILING]
    # Every remaining ceiling must be one of: external spend, futile repetition,
    # context budget, or the final runway stop.
    allowed = {
        "browser_max_actions_per_task", "search_budget_per_task",
        "repeat_failure_block_after", "repeat_success_block_after",
        "tool_output_max_chars", "browser_snapshot_max_chars",
        "failed_output_max_chars", "tool_timeout_default_s",
        "supervisor_turns_max", "delegate_failures_max",
        # Context-data bounds, not execution stops. Snapshot already existed;
        # resume context bounds the complete additional wake-message block.
        "browser_snapshot_max_chars", "resume_context_max_chars",
        # Feed limits bound quoted text and supplied interest memory; they never
        # refuse a tool call, cancel a turn, or cap the model's execution rounds.
        "feed_preview_words", "feed_memory_topics",
    }
    assert {c.name for c in ceilings} <= allowed, (
        "New CEILING added — justify it against limits.py's policy, or make it a HINT."
    )


def test_tool_timeouts_read_the_shared_default():
    from src.core.tools import tool_timeouts

    assert tool_timeouts.get_tool_timeout("some_unlisted_tool") == limits.get(
        "tool_timeout_default_s"
    )
    # Overrides still win.
    assert tool_timeouts.get_tool_timeout("build_html_file") == 600.0
