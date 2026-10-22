"""Unit tests for the Reflective-Lessons admission gatekeeper."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.lesson_gatekeeper import LessonGatekeeper, is_outage_shaped

TOOLS = {"web_search", "hubspace_control", "get_datetime", "open_app"}


def _lesson(scope="tools", lesson="x", evidence="", confidence=0.9):
    return {"scope": scope, "lesson": lesson, "evidence": evidence, "confidence": confidence}


def test_admits_clean_lesson():
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(lesson="Use web_search for stock prices.", confidence=0.9)])
    assert len(out.admitted) == 1
    assert out.admitted[0]["status"] == "provisional"
    assert not out.rejected


def test_rejects_deprecated_tool_reference():
    """The deep_research case: a lesson naming a tool no longer in the registry is stale."""
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(
        lesson="Avoid deep_research for simple stock lookups; use web_search instead.",
        evidence="deep_research failed 8 times.", confidence=0.95)])
    assert not out.admitted
    assert len(out.rejected) == 1
    assert "deep_research" in out.rejected[0]["reason"]


def test_rejects_deprecated_tool_in_subject_position():
    """'deep_research is unreliable...' — deprecated tool as the SUBJECT, not after a verb."""
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(scope="tools",
        lesson="deep_research is unreliable for simple lookups; prefer web_search.",
        evidence="deep_research failed 8 times.", confidence=0.9)])
    assert not out.admitted
    assert "deep_research" in out.rejected[0]["reason"]


def test_confidence_floor():
    gk = LessonGatekeeper(available_tools=TOOLS, confidence_floor=0.7)
    out = gk.filter([_lesson(lesson="Open apps with open_app.", confidence=0.5)])
    assert not out.admitted
    assert "confidence" in out.rejected[0]["reason"]


def test_invalid_scope_and_empty():
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(scope="banana", lesson="something"),
                     _lesson(scope="user", lesson="")])
    assert not out.admitted
    assert len(out.rejected) == 2


def test_dedup_against_existing():
    gk = LessonGatekeeper(available_tools=TOOLS,
                          existing_lessons=["The user prefers the conclusion first then detail."])
    out = gk.filter([_lesson(scope="user",
                             lesson="The user prefers the conclusion first, then the detail.",
                             confidence=0.9)])
    assert not out.admitted
    assert "duplicate of an existing" in out.rejected[0]["reason"]


def test_dedup_within_batch():
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([
        _lesson(scope="user", lesson="The user prefers dark mode everywhere.", confidence=0.9),
        _lesson(scope="user", lesson="The user prefers dark mode themes everywhere.", confidence=0.8),
    ])
    assert len(out.admitted) == 1  # higher-confidence one kept, the near-dup rejected
    assert any("another candidate" in r["reason"] for r in out.rejected)


def test_per_cycle_cap_keeps_highest_confidence():
    gk = LessonGatekeeper(available_tools=TOOLS, max_per_cycle=2)
    cands = [
        _lesson(lesson="Launch Spotify with open_app when the user starts working.", confidence=0.71),
        _lesson(scope="user", lesson="The user works late evenings, not mornings.", confidence=0.99),
        _lesson(lesson="Use get_datetime before scheduling reminders.", confidence=0.85),
    ]
    out = gk.filter(cands)
    assert len(out.admitted) == 2
    confs = sorted(a["confidence"] for a in out.admitted)
    assert confs == [0.85, 0.99]  # the 0.71 got capped out
    assert any("cap" in r["reason"] for r in out.rejected)


def test_rejects_behavior_suppression_lessons():
    """The exact anti-pattern the judge produced: 'stop asking' without the fact."""
    gk = LessonGatekeeper(available_tools=TOOLS)
    bad = [
        _lesson(scope="user", lesson="When the user says 'my lamp', directly control the"
                "lamp device without asking for device name clarification.", confidence=0.9),
        _lesson(scope="tools", lesson="Don't ask the user to confirm the device name.", confidence=0.9),
        _lesson(scope="user", lesson="Never prompt the user to clarify ambiguous requests.", confidence=0.9),
    ]
    out = gk.filter(bad)
    assert not out.admitted
    assert all("suppression" in r["reason"] for r in out.rejected)


def test_grounded_fact_lesson_still_passes():
    """The CORRECT phrasing — stating the fact — must not be caught by the guard."""
    gk = LessonGatekeeper(available_tools=TOOLS | {"list_devices"})
    out = gk.filter([_lesson(scope="user",
        lesson="The user's only lamp is named 'bedroom' (per list_devices); map 'my lamp' to it.",
        confidence=0.9)])
    assert len(out.admitted) == 1
    assert not out.rejected


def test_param_name_not_mistaken_for_dead_tool():
    """Regression: 'device_name' is a PARAMETER, not a deprecated tool — must not be rejected.

    This is the real lamp lesson the judge produced; it was being false-rejected because
    device_name matched a naive snake_case token check.
    """
    gk = LessonGatekeeper(available_tools={"hubspace_control", "web_search"})
    out = gk.filter([_lesson(scope="tools",
        lesson="The user's lamp is controlled via hubspace_control; device_name='lamp' resolves to 'Bedroom'.",
        evidence="hubspace_control(action=turn_on, device_name=lamp) -> OK: Turned on Bedroom.",
        confidence=0.9)])
    assert len(out.admitted) == 1, out.rejected
    assert not out.rejected


def test_no_registry_skips_dead_tool_guard():
    """Without a tool snapshot we can't judge staleness — don't false-reject."""
    gk = LessonGatekeeper(available_tools=None)
    out = gk.filter([_lesson(lesson="Avoid deep_research; use web_search.", confidence=0.9)])
    assert len(out.admitted) == 1


def test_parameter_token_not_flagged_as_dead_tool():
    """device_name is a PARAMETER (used after a tool-verb) — must not read as a dead tool."""
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(
        scope="tools",
        lesson="The user's lamp maps to use device_name='Bedroom' in hubspace_control.",
        confidence=0.9)])
    assert len(out.admitted) == 1, out.rejected


def test_evidence_tool_paraphrase_does_not_veto_lesson():
    """A loose tool name in EVIDENCE must not reject an otherwise-clean lesson."""
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(
        scope="user",
        lesson="Sam is the user's partner.",
        evidence="seen via google_drive_read_file in shared planning doc",
        confidence=0.95)])
    assert len(out.admitted) == 1, out.rejected


def test_dead_tool_in_lesson_text_still_rejected():
    """Regression guard: real deprecated tool in the LESSON text is still caught."""
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(lesson="deep_research is unreliable; use web_search.", confidence=0.9)])
    assert not out.admitted and out.rejected


# ── outage-shaped lessons (#640) ──────────────────────────────────────────────


def test_is_outage_shaped_detects_transient_phrasing():
    assert is_outage_shaped(
        "Sandboxed execution tools are currently returning 401 Unauthorized errors "
        "due to malformed authorization headers.")
    assert is_outage_shaped("The API is down right now.")
    assert is_outage_shaped("hubspace_control is throwing a 503 at the moment.")


def test_is_outage_shaped_false_for_durable_facts():
    assert not is_outage_shaped("web_search has a max of 10 queries per call.")
    assert not is_outage_shaped("The user's only lamp is named 'bedroom'.")


def test_rejects_outage_shaped_lesson():
    """The exact #640 regression: transient infra state frozen as permanent doctrine."""
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(
        scope="tools",
        lesson="Sandboxed execution tools (run_sandboxed_bash, run_sandboxed_python) are "
               "currently returning 401 Unauthorized errors due to malformed authorization "
               "headers.",
        confidence=0.9)])
    assert not out.admitted
    assert "outage-shaped" in out.rejected[0]["reason"]


def test_durable_fact_with_number_not_mistaken_for_outage():
    """A genuinely durable fact must not be caught by the outage guard just because it
    mentions a number or the word 'currently' in an unrelated sense."""
    gk = LessonGatekeeper(available_tools=TOOLS)
    out = gk.filter([_lesson(
        scope="tools", lesson="web_search accepts at most 10 queries per call.",
        confidence=0.9)])
    assert len(out.admitted) == 1, out.rejected
