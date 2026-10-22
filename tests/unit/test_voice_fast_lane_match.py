"""Unit tests for voice_fast_lane regex / template helpers (polite opens, fillers)."""

from src.core.orchestration.nodes.voice_fast_lane import (
    ACTION_OPEN,
    ACTION_SEARCH,
    is_instant_open_command,
    match_fast_lane,
    template_filler_for,
)


def test_canonical_open_matches_regex():
    hit = match_fast_lane("open youtube", use_embedding_fallback=False)
    assert hit is not None
    assert hit.action == ACTION_OPEN
    assert hit.slot == "youtube"
    assert hit.method == "regex"


def test_polite_prefix_open_matches_regex():
    for phrase in (
        "can you open youtube",
        "could you open youtube",
        "please open youtube",
        "would you open youtube",
    ):
        hit = match_fast_lane(phrase, use_embedding_fallback=False)
        assert hit is not None, phrase
        assert hit.action == ACTION_OPEN, phrase
        assert hit.slot == "youtube", phrase


def test_trailing_please_stripped_from_slot():
    hit = match_fast_lane("open youtube please", use_embedding_fallback=False)
    assert hit is not None
    assert hit.action == ACTION_OPEN
    assert hit.slot == "youtube"


def test_is_instant_open_command():
    assert is_instant_open_command("open youtube") is True
    assert is_instant_open_command("can you open gmail") is True
    assert is_instant_open_command("search for pizza") is False
    assert is_instant_open_command("check my email") is False


def test_template_filler_skips_open_and_labels_search():
    assert template_filler_for("open youtube") is None
    assert template_filler_for("search for pizza places") == "Looking that up."
    assert template_filler_for("look up the weather") == "Looking that up."
    assert template_filler_for("check my email") is None


def test_search_still_classifies():
    hit = match_fast_lane("search for pizza places near me", use_embedding_fallback=False)
    assert hit is not None
    assert hit.action == ACTION_SEARCH


def test_open_inside_phrase_is_not_imperative_open():
    """Embedding override must not treat 'open' mid-sentence as a launch."""
    from src.core.orchestration.nodes.voice_fast_lane import _OPEN_IMPERATIVE_RE

    assert _OPEN_IMPERATIVE_RE.search("find open source tools") is None
    assert _OPEN_IMPERATIVE_RE.search("open youtube") is not None
    assert is_instant_open_command("find open source tools") is False
