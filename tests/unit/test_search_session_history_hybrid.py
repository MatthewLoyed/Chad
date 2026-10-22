"""Unit tests for hybrid search_session_history (chat FTS + session_log fallback)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tools.system.utilities.search_session_history import SearchSessionHistoryTool


@pytest.mark.asyncio
async def test_prefers_chat_history_hits():
    repo = MagicMock()
    repo.search_chat_messages = AsyncMock(
        return_value=[
            {
                "id": "m1",
                "conversation_id": "c1",
                "conversation_title": "Links",
                "role": "assistant",
                "created_at": "2026-07-01T12:00:00",
                "content": "watch https://youtu.be/abc",
                "snippet": "…https://youtu.be/abc…",
                "rank": -1.2,
            }
        ]
    )
    memory = MagicMock()
    memory.search_session_history = MagicMock(
        return_value=[{"content": "vector-only hit", "timestamp": "2026-06-01", "similarity": 0.9}]
    )

    tool = SearchSessionHistoryTool(memory_engine=memory, chat_history_repo=repo)
    out = await tool.execute(query="youtu.be/abc", limit=5)

    assert out["success"] is True
    assert out["count"] == 1
    hit = out["results"][0]
    assert hit["source"] == "chat_history"
    assert hit["conversation_id"] == "c1"
    assert "youtu.be" in hit["snippet"]
    memory.search_session_history.assert_not_called()


@pytest.mark.asyncio
async def test_fills_from_vector_when_fts_undershoots():
    repo = MagicMock()
    repo.search_chat_messages = AsyncMock(return_value=[])
    memory = MagicMock()
    memory.search_session_history = MagicMock(
        return_value=[
            {
                "content": "[Session 2026-07-10] User: golfport?\nChad: dinner at 7",
                "timestamp": "2026-07-10T00:00:00+00:00",
                "similarity": 0.81,
                "metadata": {"thread_id": "thread-9"},
            }
        ]
    )

    tool = SearchSessionHistoryTool(memory_engine=memory, chat_history_repo=repo)
    out = await tool.execute(query="golfport", limit=5)

    assert out["success"] is True
    assert out["count"] == 1
    hit = out["results"][0]
    assert hit["source"] == "session_log"
    assert hit["conversation_id"] == "thread-9"
    assert "golfport" in hit["snippet"].lower() or "dinner" in hit["snippet"].lower()
    memory.search_session_history.assert_called_once()


@pytest.mark.asyncio
async def test_dedupes_message_id_across_sources():
    repo = MagicMock()
    repo.search_chat_messages = AsyncMock(
        return_value=[
            {
                "id": "m1",
                "conversation_id": "c1",
                "conversation_title": "A",
                "role": "user",
                "created_at": "2026-07-01",
                "snippet": "same text",
                "rank": 0.0,
            }
        ]
    )
    memory = MagicMock()
    # Would only be consulted if FTS undershoots — with limit=1 it should not be.
    memory.search_session_history = MagicMock(return_value=[])

    tool = SearchSessionHistoryTool(memory_engine=memory, chat_history_repo=repo)
    out = await tool.execute(query="same", limit=1)
    assert out["count"] == 1
    assert out["results"][0]["message_id"] == "m1"


@pytest.mark.asyncio
async def test_validate_requires_query():
    tool = SearchSessionHistoryTool(memory_engine=MagicMock(), chat_history_repo=MagicMock())
    ok, err = tool.validate_params(query="")
    assert ok is False and err
    ok2, _ = tool.validate_params(query="hello")
    assert ok2 is True
