"""Unit tests for SessionHistoryClient — search over category=session_log."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.core.intelligence.session_history_client import SessionHistoryClient


def _configure(monkeypatch, **overrides):
    from src.shared.config_manager import ConfigManager

    defaults = {
        "session_history.enabled": True,
        "session_history.max_results": 3,
        "session_history.relevance_threshold": 0.25,
        "knowledge_graph.max_item_chars": 160,
    }
    defaults.update(overrides)

    def fake_get(key_path, default=None):
        return defaults.get(key_path, default)

    monkeypatch.setattr(ConfigManager, "get", staticmethod(fake_get))


@pytest.mark.asyncio
async def test_search_returns_none_when_disabled(monkeypatch):
    _configure(monkeypatch, **{"session_history.enabled": False})
    client = SessionHistoryClient()
    result = await client.search("anything")
    assert result is None


@pytest.mark.asyncio
async def test_search_returns_none_on_internal_error(monkeypatch):
    """Search failures must degrade to None, never raise into the graphrag injector."""
    _configure(monkeypatch)
    client = SessionHistoryClient()
    client._get_engine = MagicMock(side_effect=RuntimeError("boom"))
    result = await client.search("anything")
    assert result is None


def test_search_sessions_filters_by_category_from_vector_hits():
    engine = MagicMock()
    engine.get_embedding.return_value = [0.1, 0.2, 0.3]
    engine.repository.vector_search_memories.return_value = [
        {"content": "past exchange about anime", "similarity": 0.6, "category": "session_log", "metadata": {}},
        {"content": "unrelated activity row", "similarity": 0.9, "category": "personal_index", "metadata": {}},
        {"content": "drive file row", "similarity": 0.8, "category": "drive_catalog", "metadata": {}},
    ]

    results = SessionHistoryClient._search_sessions(engine, "anime", limit=5)

    assert len(results) == 1
    assert results[0]["content"] == "past exchange about anime"


def test_search_sessions_keyword_fallback_when_no_embedding():
    engine = MagicMock()
    engine.get_embedding.return_value = None
    engine.repository.search_memories_by_category.return_value = [
        {"content": "fallback exchange", "metadata": {}}
    ]

    results = SessionHistoryClient._search_sessions(engine, "anime", limit=5)

    assert len(results) == 1
    assert results[0]["content"] == "fallback exchange"
    engine.repository.search_memories_by_category.assert_called_once()
    _, kwargs = engine.repository.search_memories_by_category.call_args
    assert kwargs["category"] == "session_log"
