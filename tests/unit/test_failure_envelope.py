"""Tests for the shared failure envelope (src/core/tools/failure.py).

These lock the two properties the harness depends on:
  1. Every failure a model sees is CLASSIFIED and carries NEXT_ACTIONS.
  2. A failure costs bounded context, no matter how big the underlying error was.
"""

import asyncio

import pytest

from src.core.tools import failure


# ── classification ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message,expected",
    [
        ("401: Unauthorized, invalid api key", failure.AUTH),
        ("403 Forbidden: permission denied", failure.PERMISSION),
        ("429 rate limit exceeded, slow down", failure.RATE_LIMIT),
        ("Read timed out after 60s", failure.TIMEOUT),
        ("503 Service Unavailable", failure.UNAVAILABLE),
        ("404: file not found", failure.NOT_FOUND),
        ("400 invalid parameter 'limit'", failure.BAD_INPUT),
        ("something nobody has ever seen", failure.UNKNOWN),
    ],
)
def test_classify_maps_common_errors(message, expected):
    assert failure.classify(message) == expected


def test_auth_beats_generic_4xx():
    """A 401 must not degrade into bad_input — that would make it retryable and
    send the model into a credentials retry loop."""
    assert failure.classify("400 invalid api key supplied") == failure.AUTH
    assert failure.failure("401 unauthorized")["retryable"] is False


def test_classify_uses_exception_type_when_text_is_useless():
    assert failure.classify("", exc=asyncio.TimeoutError()) == failure.TIMEOUT
    assert failure.classify("boom", exc=PermissionError("boom")) == failure.PERMISSION


def test_registered_domain_classifier_wins():
    sentinel = "totally-unique-domain-marker-xyz"

    def matcher(msg):
        return failure.EXPIRED if sentinel in msg else None

    failure.register_classifier(matcher)
    try:
        # Generic patterns would call this "unavailable" (502); the domain says expired.
        assert failure.classify(f"502 {sentinel}") == failure.EXPIRED
    finally:
        failure._CUSTOM.remove(matcher)


# ── the envelope ─────────────────────────────────────────────────────────────


def test_envelope_has_everything_a_model_needs():
    payload = failure.failure("Read timed out", tool_name="web_search")
    assert payload["success"] is False
    assert payload["failure_class"] == failure.TIMEOUT
    assert payload["retryable"] is True
    assert payload["next_actions"]
    # The rendered text is what the model actually reads — guidance must be in it.
    assert "TOOL_FAILED[timeout] web_search:" in payload["error"]
    assert "NEXT_ACTIONS" in payload["error"]


def test_non_retryable_render_says_retrying_is_pointless():
    text = failure.failure("403 forbidden", tool_name="gmail")["error"]
    assert "RETRY: pointless" in text


def test_ensure_envelope_upgrades_a_legacy_bare_error():
    legacy = {"success": False, "error": "404: no such file /tmp/nope.txt"}
    upgraded = failure.ensure_envelope(legacy, tool_name="read_file")
    assert upgraded["failure_class"] == failure.NOT_FOUND
    assert "NEXT_ACTIONS" in upgraded["error"]
    assert upgraded["detail"].startswith("404")


def test_ensure_envelope_leaves_self_classified_tools_alone():
    """A tool that classified itself knows more than the generic matcher."""
    own = failure.failure(
        "sandbox died", failure_class=failure.EXPIRED, next_actions=["retry once"]
    )
    assert failure.ensure_envelope(own, tool_name="run_sandboxed_bash") is own


def test_ensure_envelope_preserves_other_keys():
    legacy = {"success": False, "error": "boom", "partial_results": [1, 2, 3]}
    upgraded = failure.ensure_envelope(legacy)
    assert upgraded["partial_results"] == [1, 2, 3]


def test_ensure_envelope_ignores_successes():
    ok = {"success": True, "result": "fine"}
    assert failure.ensure_envelope(ok) is ok


# ── context budget ───────────────────────────────────────────────────────────


def test_envelope_is_bounded_even_for_a_huge_error():
    """A 200KB stack trace must not become 200KB of the model's context."""
    payload = failure.failure("x" * 200_000, tool_name="run_sandboxed_python")
    assert len(payload["detail"]) <= failure.MAX_DETAIL_CHARS + 10
    assert len(payload["error"]) < 3000
    assert "NEXT_ACTIONS" in payload["error"]


def test_clip_failure_text_keeps_both_ends():
    """Guidance sits near the front of the serialized envelope and raw detail near
    the back, so neither end may be dropped."""
    text = "HEAD_MARKER" + ("z" * 10_000) + "TAIL_MARKER"
    clipped = failure.clip_failure_text(text, limit=400)
    assert "HEAD_MARKER" in clipped
    assert "TAIL_MARKER" in clipped
    assert len(clipped) < 700


def test_signature_matches_inside_serialized_json():
    """Regression: the marker is mid-line once JSON-serialized and [TOOL_DATA]-tagged.
    An anchored regex silently skipped every real failure."""
    import json

    payload = failure.failure("timed out", tool_name="web_search")
    wire = "[TOOL_DATA]\n" + json.dumps(payload)
    assert failure.failure_signature(wire) == "timeout:web_search"


def test_signature_is_none_for_ordinary_output():
    assert failure.failure_signature("[TOOL_DATA]\nHere are your search results.") is None


def test_same_class_different_detail_shares_a_signature():
    """Collapsing depends on this: two attempts that fail the same way must match
    even though their messages differ."""
    a = failure.failure("timed out after 60s", tool_name="web_search")["error"]
    b = failure.failure("timed out after 61s", tool_name="web_search")["error"]
    assert failure.failure_signature(a) == failure.failure_signature(b)


def test_different_tools_do_not_share_a_signature():
    a = failure.failure("timed out", tool_name="web_search")["error"]
    b = failure.failure("timed out", tool_name="read_file")["error"]
    assert failure.failure_signature(a) != failure.failure_signature(b)


# ── delegate failures ────────────────────────────────────────────────────────


def test_delegate_names_stay_underscored_in_the_marker():
    """Regression: a prettified 'browser agent' truncated the signature at the space,
    merging every specialist's failures into one bucket and collapsing unrelated
    messages together."""
    text = failure.delegate_timeout("browser_agent")
    assert "delegate_to_browser_agent" in text
    assert failure.failure_signature(text) == "timeout:delegate_to_browser_agent"


def test_delegate_pretty_name_still_reads_naturally_in_prose():
    assert "browser agent" in failure.delegate_timeout("browser_agent")


def test_delegate_failures_are_distinguishable_by_signature():
    sigs = {
        failure.failure_signature(failure.delegate_timeout("code_agent")),
        failure.failure_signature(failure.delegate_blocked("code_agent", 3)),
        failure.failure_signature(failure.delegate_unavailable("code_agent", "no models loaded")),
    }
    assert len(sigs) == 3


def test_delegate_unavailable_forbids_retry():
    text = failure.delegate_unavailable("code_agent", "model not found")
    assert "RETRY: pointless" in text
    assert "do NOT delegate to this agent again" in text


def test_is_infra_failure_detects_model_load_problems():
    assert failure.is_infra_failure("LM Studio: no models loaded")
    assert failure.is_infra_failure(Exception("connection refused"))
    assert not failure.is_infra_failure("the user's file was not found")
