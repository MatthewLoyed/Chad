"""Unit tests for the capability-gap clustering + suggestion gatekeeper (pure logic)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.capability_gaps import (
    cluster_gaps, SuggestionGatekeeper, _cosine,
)


# ── clustering ──────────────────────────────────────────────────────────────

def _gap(text, emb=None, gap_id=None):
    return {"id": gap_id, "query_text": text, "query_embedding": emb}


def test_cosine_basics():
    assert _cosine([1, 0], [1, 0]) == 1.0
    assert _cosine([1, 0], [0, 1]) == 0.0
    assert _cosine([], [1]) == 0.0  # mismatched/empty → safe 0


def test_clusters_similar_embeddings_together():
    # Two near-identical vectors + one orthogonal → 2 clusters.
    gaps = [
        _gap("send a telegram message", [1.0, 0.0, 0.0]),
        _gap("message someone on telegram", [0.98, 0.02, 0.0]),
        _gap("render a 3d model", [0.0, 0.0, 1.0]),
    ]
    clusters = cluster_gaps(gaps, sim_threshold=0.82)
    assert len(clusters) == 2
    # Largest-first ordering: the telegram pair is the big cluster.
    assert len(clusters[0]) == 2


def test_falls_back_to_exact_text_without_embeddings():
    gaps = [
        _gap("export to notion"),
        _gap("export to notion"),
        _gap("book a flight"),
    ]
    clusters = cluster_gaps(gaps)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]


def test_singletons_when_all_dissimilar():
    gaps = [
        _gap("a", [1.0, 0.0, 0.0]),
        _gap("b", [0.0, 1.0, 0.0]),
        _gap("c", [0.0, 0.0, 1.0]),
    ]
    clusters = cluster_gaps(gaps, sim_threshold=0.82)
    assert len(clusters) == 3


# ── suggestion gatekeeper ─────────────────────────────────────────────────────

def _cand(kind="tool", title="send_telegram", description="Send a Telegram message to a contact.",
          confidence=0.9):
    return {"kind": kind, "title": title, "description": description, "confidence": confidence}


def test_admits_clean_suggestion():
    gk = SuggestionGatekeeper(existing_capabilities=["web_search: search the web"])
    out = gk.filter([_cand()])
    assert len(out.admitted) == 1
    assert not out.rejected


def test_rejects_low_confidence():
    gk = SuggestionGatekeeper(confidence_floor=0.7)
    out = gk.filter([_cand(confidence=0.4)])
    assert not out.admitted
    assert "confidence" in out.rejected[0]["reason"]


def test_rejects_invalid_kind_or_empty():
    gk = SuggestionGatekeeper()
    out = gk.filter([_cand(kind="widget"), _cand(title="")])
    assert not out.admitted
    assert len(out.rejected) == 2


def test_rejects_capability_that_already_exists():
    """Should never suggest rebuilding a tool Chad already has."""
    gk = SuggestionGatekeeper(existing_capabilities=[
        "send_telegram_message: Send a Telegram message to a contact or chat."])
    out = gk.filter([_cand()])
    assert not out.admitted
    assert "already exists" in out.rejected[0]["reason"]


def test_rejects_duplicate_of_pending_suggestion():
    gk = SuggestionGatekeeper(existing_suggestions=[
        "send_telegram: Send a Telegram message to a contact."])
    out = gk.filter([_cand()])
    assert not out.admitted
    assert "existing pending suggestion" in out.rejected[0]["reason"]


def test_dedups_within_batch():
    gk = SuggestionGatekeeper()
    out = gk.filter([
        _cand(title="send_telegram", confidence=0.95),
        _cand(title="telegram_sender", description="Send a Telegram message to a contact.",
              confidence=0.8),
    ])
    assert len(out.admitted) == 1
    assert len(out.rejected) == 1
    assert "another candidate this cycle" in out.rejected[0]["reason"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
