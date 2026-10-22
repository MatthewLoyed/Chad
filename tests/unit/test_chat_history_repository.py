import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository

@pytest.mark.asyncio
async def test_get_conversation_success():
    # Mock supabase manager
    mock_supabase = MagicMock()
    mock_supabase.async_query = AsyncMock(return_value=[
        {"id": "test-uuid-1234", "title": "New Chat"}
    ])
    
    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        repo = ChatHistoryRepository()
        conv = await repo.get_conversation("test-uuid-1234")
        
        assert conv is not None
        assert conv["id"] == "test-uuid-1234"
        assert conv["title"] == "New Chat"
        mock_supabase.async_query.assert_called_once_with(
            "conversations",
            filters={"id": "eq.test-uuid-1234"}
        )

@pytest.mark.asyncio
async def test_get_conversation_not_found():
    # Mock supabase manager returning empty list
    mock_supabase = MagicMock()
    mock_supabase.async_query = AsyncMock(return_value=[])
    
    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        repo = ChatHistoryRepository()
        conv = await repo.get_conversation("non-existent-uuid")
        
        assert conv is None
        mock_supabase.async_query.assert_called_once_with(
            "conversations",
            filters={"id": "eq.non-existent-uuid"}
        )

@pytest.mark.asyncio
async def test_get_conversations_orders_pinned_first():
    mock_supabase = MagicMock()
    mock_supabase.async_query = AsyncMock(return_value=[])

    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        repo = ChatHistoryRepository()
        await repo.get_conversations(limit=10, offset=0)

        mock_supabase.async_query.assert_called_once_with(
            "conversations",
            limit=10,
            offset=0,
            order="is_pinned.desc,updated_at.desc",
        )

@pytest.mark.asyncio
async def test_update_conversation_pinned():
    mock_supabase = MagicMock()
    mock_supabase.async_update = AsyncMock(return_value=True)

    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        repo = ChatHistoryRepository()
        ok = await repo.update_conversation_pinned("test-uuid-1234", True)

        assert ok is True
        mock_supabase.async_update.assert_called_once_with(
            "conversations",
            filters={"id": "eq.test-uuid-1234"},
            data={"is_pinned": True},
        )

@pytest.mark.asyncio
async def test_update_conversation_project():
    mock_supabase = MagicMock()
    mock_supabase.async_update = AsyncMock(return_value=True)

    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        repo = ChatHistoryRepository()
        ok = await repo.update_conversation_project("test-uuid-1234", "C:\\Projects\\AIAssistant")

        assert ok is True
        mock_supabase.async_update.assert_called_once_with(
            "conversations",
            filters={"id": "eq.test-uuid-1234"},
            data={"project_path": "C:\\Projects\\AIAssistant"},
        )


@pytest.mark.asyncio
async def test_truncate_conversation_after_supabase():
    mock_supabase = MagicMock()
    # Supabase expects UUID format for conversation_id
    valid_uuid = "11111111-2222-3333-4444-555555555555"
    m1_id = "aaaaaaaa-1111-1111-1111-111111111111"
    m2_id = "bbbbbbbb-2222-2222-2222-222222222222"
    m3_id = "cccccccc-3333-3333-3333-333333333333"

    mock_supabase.async_query = AsyncMock(return_value=[
        {"id": m1_id, "role": "user", "content": "hello"},
        {"id": m2_id, "role": "assistant", "content": "hi"},
        {"id": m3_id, "role": "user", "content": "retried prompt"},
    ])
    mock_supabase.async_update = AsyncMock(return_value=True)

    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        repo = ChatHistoryRepository()
        ok = await repo.truncate_conversation_after(valid_uuid, keep_count=2)

        assert ok is True
        # m3_id (index 2 onwards) should be archived
        mock_supabase.async_update.assert_called_once()
        call_args = mock_supabase.async_update.call_args
        assert call_args[0][0] == "messages"
        assert call_args[1]["filters"] == {"id": f"in.({m3_id})"}
        assert call_args[1]["data"]["is_archived"] is True

