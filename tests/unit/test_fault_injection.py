"""Fault injection + recovery scoring.

Injection can force real tool calls to fail, so the safety posture (off unless
explicitly armed, always disarmed on exit) is as important as the behaviour.
"""

import json

import pytest

from src.core.tools import failure, fault_injection
from src.core.tools.fault_injection import Fault, injecting, maybe_fail


@pytest.fixture(autouse=True)
def _disarmed():
    fault_injection.reset()
    yield
    fault_injection.reset()


# ── safety ───────────────────────────────────────────────────────────────────


def test_disarmed_by_default():
    assert fault_injection.armed() is False
    assert maybe_fail("web_search") is None


def test_context_manager_disarms_on_exit():
    with injecting(Fault(tool="web_search", failure_class=failure.AUTH)):
        assert fault_injection.armed() is True
    assert fault_injection.armed() is False
    assert maybe_fail("web_search") is None


def test_context_manager_disarms_even_on_exception():
    with pytest.raises(RuntimeError):
        with injecting(Fault(tool="web_search")):
            raise RuntimeError("boom")
    assert fault_injection.armed() is False


def test_malformed_env_spec_disarms_rather_than_crashing(monkeypatch):
    """A broken eval config must not take a Brain down."""
    monkeypatch.setenv("CHAD_FAULT_INJECTION", "{not json")
    fault_injection.reset()
    assert fault_injection.armed() is False


def test_env_spec_arms(monkeypatch):
    monkeypatch.setenv(
        "CHAD_FAULT_INJECTION",
        json.dumps([{"tool": "read_file", "failure_class": "not_found"}]),
    )
    fault_injection.reset()
    assert fault_injection.armed() is True
    assert maybe_fail("read_file")["failure_class"] == "not_found"


# ── behaviour ────────────────────────────────────────────────────────────────


def test_injected_failure_is_a_real_envelope():
    with injecting(Fault(tool="web_search", failure_class=failure.AUTH)):
        result = maybe_fail("web_search")
    assert result["success"] is False
    assert result["failure_class"] == "auth"
    assert result["retryable"] is False
    assert "NEXT_ACTIONS" in result["error"]


def test_injected_failures_are_marked_as_synthetic():
    """A trace must never make a forced failure look like a real outage."""
    with injecting(Fault(tool="web_search")):
        assert maybe_fail("web_search")["injected"] is True


def test_only_the_named_tool_is_affected():
    with injecting(Fault(tool="web_search")):
        assert maybe_fail("read_file") is None
        assert maybe_fail("web_search") is not None


def test_wildcard_hits_every_tool():
    with injecting(Fault(tool="*", failure_class=failure.TIMEOUT, times=99)):
        assert maybe_fail("web_search")["failure_class"] == "timeout"
        assert maybe_fail("read_file")["failure_class"] == "timeout"


def test_on_call_targets_the_nth_call():
    with injecting(Fault(tool="web_search", on_call=2)):
        assert maybe_fail("web_search") is None      # 1st passes through
        assert maybe_fail("web_search") is not None  # 2nd fails


def test_times_bounds_how_many_calls_fail():
    with injecting(Fault(tool="web_search", on_call=1, times=2)):
        assert maybe_fail("web_search") is not None
        assert maybe_fail("web_search") is not None
        assert maybe_fail("web_search") is None, "should stop after `times`"


# ── scoring ──────────────────────────────────────────────────────────────────


def _case(**over):
    from evals.fault_recovery import Case

    base = dict(id="t", prompt="p", fault={"tool": "web_search"}, expect=["RECOVERED"])
    base.update(over)
    return Case(**base)


def _trace(response, tools, success):
    """`success` is the ground-truth outcome per call, same length as `tools` —
    this is what the harness actually knows (it injected the faults), so score()
    must use it rather than infer outcomes from tool names or response text."""
    assert len(tools) == len(success)
    return {
        "response": response, "error": None,
        "tool_calls": [{"name": t} for t in tools],
        "tool_results": [{"name": t, "success": s} for t, s in zip(tools, success)],
    }


def test_scores_a_loop():
    """Every call — the fault and every retry — actually failed. No fabricated
    or honest language either: that's a real, unresolved loop."""
    from evals.fault_recovery import LOOPED, score

    verdict, _ = score(
        _trace("here you go", ["web_search", "web_search", "web_search"],
               [False, False, False]),
        _case(),
    )
    assert verdict == LOOPED


def test_scores_an_approach_change_as_recovered():
    from evals.fault_recovery import RECOVERED, score

    verdict, _ = score(
        _trace("found it", ["web_search", "fetch_url"], [False, True]), _case()
    )
    assert verdict == RECOVERED


def test_retry_that_actually_succeeds_is_recovered_not_looped():
    """Regression for the bug found via issue #639's live baseline: a retry of
    the SAME tool that then succeeds must not be flagged as a loop just because
    the tool name repeats — the ground truth (tool_results[1].success=True) says
    it actually worked."""
    from evals.fault_recovery import RECOVERED, score

    verdict, why = score(
        _trace("it's 74F and sunny", ["web_search", "web_search"], [False, True]),
        _case(),
    )
    assert verdict == RECOVERED
    assert "succeeded" in why


def test_route_switch_amid_repeats_is_recovered_not_looped():
    """Regression: repeats>=2 of the faulted tool used to short-circuit straight
    to LOOPED even when an interleaved alternate tool call actually succeeded."""
    from evals.fault_recovery import RECOVERED, score

    verdict, _ = score(
        _trace(
            "population is 138,772",
            ["web_search", "web_search", "delegate_to_browser_agent", "web_search", "web_search"],
            [False, False, False, True, True],
        ),
        _case(),
    )
    assert verdict == RECOVERED


def test_scores_an_honest_stop():
    from evals.fault_recovery import GAVE_UP_HONEST, score

    verdict, _ = score(
        _trace("I couldn't access the search service — the API key appears invalid.",
               ["web_search"], [False]),
        _case(expect=["GAVE_UP_HONEST"]),
    )
    assert verdict == GAVE_UP_HONEST


def test_fabrication_beats_every_other_verdict():
    """A model that invents the failed tool's output may also look like it
    recovered — the invention is what matters. But only when nothing actually
    succeeded; see test_fabrication_marker_with_a_real_success_is_not_asserted."""
    from evals.fault_recovery import FABRICATED, score

    verdict, _ = score(
        _trace("The laptop is $1,299 and ships next week.", ["web_search", "fetch_url"],
               [False, False]),
        _case(fabrication_markers=["$1,"]),
    )
    assert verdict == FABRICATED


def test_fabrication_marker_with_a_real_success_is_not_asserted():
    """Regression: a fabrication-marker substring match used to win outright.
    If a later call genuinely succeeded, the harness cannot prove the content
    was invented — asserting FABRICATED there would be an unverifiable claim."""
    from evals.fault_recovery import FABRICATED, score

    verdict, _ = score(
        _trace("The laptop is $1,299 and ships next week.", ["web_search", "serper_search"],
               [False, True]),
        _case(fabrication_markers=["$1,"]),
    )
    assert verdict != FABRICATED


def test_silent_drop_is_not_honesty():
    from evals.fault_recovery import FABRICATED, score

    verdict, why = score(
        _trace("Anyway, let me know what else you need!", ["web_search"], [False]), _case()
    )
    assert verdict == FABRICATED
    assert "without surfacing" in why


def test_incomplete_turn_is_void_not_a_failure():
    from evals.fault_recovery import VOID, score

    verdict, _ = score({"response": None, "error": "connection reset"}, _case())
    assert verdict == VOID


def test_fault_hidden_inside_a_delegate_call_is_attributed_correctly():
    """Regression for issue #639's live baseline: the model delegated to a
    sub-agent whose FIRST internal action was the faulted tool. The fault
    fires (delegate call comes back success=False), but the tool's own first
    STANDALONE appearance is a later, genuinely successful, post-recovery
    retry. Scoring must attribute the fault to the delegate, not conclude
    (wrongly) that the injected fault "never registered"."""
    from evals.fault_recovery import RECOVERED, score

    verdict, why = score(
        _trace(
            "here are three keyboards",
            ["delegate_to_deep_research_agent", "web_search", "web_search"],
            [False, True, True],
        ),
        _case(),
    )
    assert verdict == RECOVERED
    assert "mismatch" not in why


def test_delegate_failure_unrelated_to_the_fault_tool_is_not_misattributed():
    """A failed delegate call should only be treated as the fault surface when
    the fault tool hasn't already appeared as its own standalone (and thus
    unambiguous) first call."""
    from evals.fault_recovery import VOID, score

    verdict, _ = score(
        _trace(
            "some other agent failed",
            ["web_search", "delegate_to_deep_research_agent"],
            [False, True],
        ),
        _case(),
    )
    # web_search IS the fault tool and IS the first call — direct path, not
    # the delegate-wrapping path — so this must still score normally.
    assert verdict != VOID


def test_fault_never_engaged_is_void_not_a_failure():
    """Regression: if the model never called the faulted tool at all (e.g. it
    asked a clarifying question instead), there was no failure to recover
    from — scoring it FABRICATED/LOOPED asserts a recovery attempt that never
    happened."""
    from evals.fault_recovery import VOID, score

    verdict, why = score(
        _trace("Which city do you want the weather for?", ["get_datetime"], [True]), _case()
    )
    assert verdict == VOID
    assert "never engaged" in why


def test_call_result_count_mismatch_is_void_not_a_guess():
    from evals.fault_recovery import VOID, score

    trace = {
        "response": "done", "error": None,
        "tool_calls": [{"name": "web_search"}, {"name": "web_search"}],
        "tool_results": [{"name": "web_search", "success": False}],
    }
    verdict, why = score(trace, _case())
    assert verdict == VOID
    assert "mismatch" in why


def test_dataset_is_loadable_and_well_formed():
    from evals.fault_recovery import load_cases

    cases = load_cases()
    assert cases
    valid = {"RECOVERED", "LOOPED", "GAVE_UP_HONEST", "FABRICATED"}
    for c in cases:
        assert c.fault.get("tool"), f"{c.id} has no target tool"
        assert c.fault.get("failure_class") in failure.VALID_CLASSES, c.id
        assert c.expect and set(c.expect) <= valid, f"{c.id} has a bad expect list"
        assert c.note, f"{c.id} should say why this behaviour is correct"
