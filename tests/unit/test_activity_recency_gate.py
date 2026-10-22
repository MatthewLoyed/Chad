"""Activity RAG recency gate — drop year-old todos from inject."""

from datetime import datetime, timedelta, timezone

from src.core.intelligence.activity_rag_client import ActivityRAGClient


def _item(days_ago: float, similarity: float = 0.9, content: str = "todo") -> dict:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "content": content,
        "similarity": similarity,
        "timestamp": ts.isoformat(),
    }


def test_recency_gate_drops_stale_items():
    results = [
        _item(10, content="recent email"),
        _item(400, content="year-old todo"),
    ]
    kept = ActivityRAGClient._apply_recency_gate(results, max_age_days=120)
    assert len(kept) == 1
    assert kept[0]["content"] == "recent email"


def test_recency_gate_boosts_newer_over_older():
    results = [
        _item(5, similarity=0.5, content="new"),
        _item(90, similarity=0.5, content="older"),
    ]
    kept = ActivityRAGClient._apply_recency_gate(results, max_age_days=120)
    by_content = {r["content"]: r["similarity"] for r in kept}
    assert by_content["new"] > by_content["older"]


def test_recency_gate_disabled_when_max_age_zero():
    results = [_item(400, content="ancient")]
    kept = ActivityRAGClient._apply_recency_gate(results, max_age_days=0)
    assert len(kept) == 1
