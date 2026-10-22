"""NarrationWithoutActionGuard — catch 'I'll now do X' instead of doing X.

False positives cost a wasted generation, so most of these tests are about the
guard staying QUIET.
"""

import pytest

from src.core.intelligence.nudges import NarrationWithoutActionGuard, NudgeManager

_DEFERRAL = (
    "Good question. I'll now search for the top wedding venues in the area, "
    "compare their pricing and capacity, and then put together a summary table "
    "for you with everything side by side."
)
_REAL_ANSWER = (
    "Here are the three venues I found, with pricing and capacity for each. "
    "The Grand Hall is the largest at 300 seats, followed by Riverside at 220, "
    "and the Old Mill at 150. Riverside is the cheapest per head."
)


def _run(guard, content, *, has_tool_call=False, tools=True):
    mgr = NudgeManager([guard])
    result = None
    for i in range(0, len(content), 20):
        result = mgr.observe(
            content_chunk=content[i:i + 20],
            has_tool_call=has_tool_call,
            tools_available=tools,
        )
        if result:
            break
    return result


def test_fires_on_a_promise_with_no_tool_call():
    result = _run(NarrationWithoutActionGuard(), _DEFERRAL)
    assert result is not None
    assert result.reason == "narration_without_tool_call"
    assert "Take the action NOW" in result.message


def test_disables_thinking_to_prevent_runaway():
    """On local inference (e.g. Bonsai-27B), restarting reasoning on a nudge creates
    a 10k token runaway thinking loop. The nudge retries with thinking disabled so the
    model emits the tool call immediately."""
    result = _run(NarrationWithoutActionGuard(), _DEFERRAL)
    assert result.disable_thinking is True


def test_fires_on_confirmation_begging():
    """Catches premature yield where model lists options and asks 'what would you like to do'."""
    menu = (
        "Here are the missing lecture files from the course. "
        "Next steps: what would you like to do? 1. Upload all missing lecture files "
        "2. Reorganize the notebook. Let me know which path you'd like."
    )
    result = _run(NarrationWithoutActionGuard(), menu)
    assert result is not None
    assert result.reason == "narration_without_tool_call"
    assert "asked the user for confirmation" in result.message


def test_silent_when_the_model_actually_called_a_tool():
    assert _run(NarrationWithoutActionGuard(), _DEFERRAL, has_tool_call=True) is None


def test_silent_when_no_tools_were_bound():
    """A pure chat turn may promise whatever it likes — there is nothing to call."""
    assert _run(NarrationWithoutActionGuard(), _DEFERRAL, tools=False) is None


def test_silent_on_a_real_answer():
    assert _run(NarrationWithoutActionGuard(), _REAL_ANSWER) is None


def test_silent_on_a_short_fragment():
    """'I'll now' could open 'I'll now show you the results' — too early to judge."""
    assert _run(NarrationWithoutActionGuard(), "I'll now") is None


def test_silent_when_disabled():
    assert _run(NarrationWithoutActionGuard(enabled=False), _DEFERRAL) is None


def test_third_person_future_is_not_a_deferral():
    """Describing what code will do is not a promise to act later."""
    content = (
        "The script will fetch each page in turn and write the results to a CSV. "
        "That will give you one row per venue, which will make the comparison "
        "much easier to scan than the raw pages."
    )
    assert _run(NarrationWithoutActionGuard(), content) is None


def test_fires_only_once_per_generation():
    guard = NarrationWithoutActionGuard()
    mgr = NudgeManager([guard])
    doubled = _DEFERRAL + " " + _DEFERRAL
    hits = 0
    for i in range(0, len(doubled), 20):
        if mgr.observe(content_chunk=doubled[i:i + 20], tools_available=True):
            hits += 1
    assert hits == 1


def test_reset_clears_state():
    guard = NarrationWithoutActionGuard()
    assert _run(guard, _DEFERRAL) is not None
    guard.reset()
    assert _run(guard, _REAL_ANSWER) is None


def test_reasoning_stream_does_not_trip_the_content_guard():
    """Planning out loud in reasoning is fine — only the visible answer counts."""
    guard = NarrationWithoutActionGuard()
    mgr = NudgeManager([guard])
    result = None
    for i in range(0, len(_DEFERRAL), 20):
        result = mgr.observe(reasoning_chunk=_DEFERRAL[i:i + 20], tools_available=True)
    assert result is None
