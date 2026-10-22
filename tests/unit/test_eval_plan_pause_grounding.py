"""Plan-approval pauses: judge the plan card, not the canned chat stub."""

from evals.judges import _is_plan_approval_pause


STUB = (
    "Here's the proposed execution plan. "
    "Review the steps above and tell me when to proceed."
)
PLAN = [{"id": "step_1", "description": "read docs", "tools": [], "status": "pending"}]


def test_plan_approval_pause_detected_via_stop_reason():
    assert _is_plan_approval_pause(
        response="", plan=PLAN, stop_reason="awaiting_approval",
    )


def test_plan_approval_pause_detected_via_canned_stub():
    assert _is_plan_approval_pause(response=STUB, plan=PLAN, stop_reason=None)


def test_normal_response_not_treated_as_plan_pause():
    assert not _is_plan_approval_pause(
        response="Wedding is May 29 at Valley Venues.",
        plan=None,
        stop_reason=None,
    )
    assert not _is_plan_approval_pause(
        response="Wedding is May 29 at Valley Venues.",
        plan=PLAN,
        stop_reason=None,
    )


def test_no_plan_never_plan_pause_even_with_stub():
    assert not _is_plan_approval_pause(response=STUB, plan=None, stop_reason="awaiting_approval")
