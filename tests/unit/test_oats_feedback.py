import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import numpy as np
import pytest

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.application.events.events import TurnFeedbackEvent
from src.infrastructure.tools.event_wrapper import ToolEventWrapper
from src.core.orchestration.context import current_turn_tool_queries_var
from scripts.oats_refine import refine_embeddings, l2_normalize

@pytest.mark.asyncio
async def test_feedback_endpoint_success():
    """Verify that _feedback_endpoint parses payload, updates database, and publishes event."""
    from src.remote.endpoints import _feedback_endpoint
    
    # Mock Starlette Request
    mock_request = MagicMock()
    mock_request.path_params = {
        "session_id": "session-123",
        "turn_id": "turn-456"
    }
    
    async def mock_json():
        return {"rating": "positive"}
    mock_request.json = mock_json
    
    # Mock Supabase update/query calls
    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    mock_supabase.async_update = AsyncMock(return_value=True)
    mock_supabase.async_query = AsyncMock(return_value=[{"metadata": {"thoughts": []}}])
    
    # Mock EventBus
    mock_event_bus = MagicMock()
    mock_request.app.state.server = MagicMock()
    mock_request.app.state.server._event_bus = mock_event_bus
    
    with patch("src.infrastructure.database.supabase_manager.get_supabase_manager", return_value=mock_supabase), \
         patch("src.infrastructure.repositories.chat_history_repository.ChatHistoryRepository.append_message", new_callable=AsyncMock), \
         patch("src.infrastructure.repositories.chat_history_repository.ChatHistoryRepository.update_message_metadata", new_callable=AsyncMock) as mock_update_meta:

        response = await _feedback_endpoint(mock_request)

        # Verify response code and body
        import json
        assert response.status_code == 200
        body = json.loads(response.body.decode())
        assert body["success"] is True

        # Verify DB updates were called. Credit assignment: a thumbs-up only rewards
        # tools that SUCCEEDED in the turn (outcome=success), not every tool row.
        mock_supabase.async_update.assert_any_call(
            "tool_outcome_log",
            filters={"turn_id": "eq.turn-456", "outcome": "eq.success"},
            data={"user_rating": "positive"}
        )
        # The rating badge is persisted via the repository (backend-agnostic: Supabase or
        # local sqlite), not a direct Supabase "messages" update anymore.
        mock_update_meta.assert_awaited_once_with("turn-456", {"user_rating": "positive"})

        # Verify event published to EventBus
        mock_event_bus.publish.assert_called_once()
        published_event = mock_event_bus.publish.call_args[0][0]
        assert isinstance(published_event, TurnFeedbackEvent)
        assert published_event.session_id == "session-123"
        assert published_event.turn_id == "turn-456"
        assert published_event.rating == "positive"


@pytest.mark.asyncio
async def test_tool_event_wrapper_logs_turn_id():
    """Verify that ToolEventWrapper logs turn_id when present in current_turn_tool_queries_var context."""
    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    mock_supabase.async_insert = AsyncMock(return_value={"success": True})
    
    query_emb = [0.01] * 768
    token = current_turn_tool_queries_var.set({
        "__query__": "parent-query",
        "__turn_id__": "turn-uuid-123",
        "test_tool": ("sub-query text", query_emb)
    })
    
    async def dummy_executor(tool_name, kwargs):
        return "outcome-ok"
        
    event_bus = MagicMock()
    
    try:
        wrapper = ToolEventWrapper.wrap_as_langchain_tool("test_tool", dummy_executor, event_bus, None)
        
        with patch("src.infrastructure.database.supabase_manager.get_supabase_manager", return_value=mock_supabase):
            result = await wrapper()
            assert result == "outcome-ok"
            
            # Allow background logging tasks to run
            await asyncio.sleep(0.01)
            
            mock_supabase.async_insert.assert_called_once_with(
                "tool_outcome_log",
                {
                    "tool_name": "test_tool",
                    "query_text": "sub-query text",
                    "query_embedding": query_emb,
                    "outcome": "success",
                    "turn_id": "turn-uuid-123"
                }
            )
    finally:
        current_turn_tool_queries_var.reset(token)


def test_oats_refine_user_feedback_overrides():
    """Verify that user rating overrides are correctly applied during refinement math."""
    mock_db = MagicMock()
    mock_db.enabled = True
    
    # 1. Mock fetch tools
    mock_db.query.side_effect = [
        # First call: return a list containing our single tool
        [{"id": "tool-1", "name": "my_tool", "embedding": [0.1] * 768}],
        # Second call: return outcomes log for the tool
        [
            # Thumbs Down (negative user_rating) overrides success -> forces repulsion weight 1.0
            {"query_embedding": [0.2] * 768, "outcome": "success", "user_rating": "negative"},
            # Thumbs Up (positive user_rating) overrides failure -> forces attraction weight 1.0
            {"query_embedding": [0.3] * 768, "outcome": "failure", "user_rating": "positive"},
            # Programmatic Success -> attraction weight = alpha (e.g. 0.3)
            {"query_embedding": [0.4] * 768, "outcome": "success", "user_rating": None},
            # Programmatic Failure -> repulsion weight = 0.5 * alpha (e.g. 0.15)
            {"query_embedding": [0.5] * 768, "outcome": "failure", "user_rating": None},
        ]
    ]
    
    # We set dry_run=True so that we don't write to DB
    with patch("scripts.oats_refine.get_supabase_manager", return_value=mock_db):
        # Trigger refinement on my_tool. We set min_outcomes=3 so the logs are processed.
        success = refine_embeddings(alpha=0.3, min_outcomes=3, dry_run=True, tool="my_tool")
        assert success is True
        
        # Verify query filters
        mock_db.query.assert_any_call(
            "tools", 
            select="id,name,embedding,embedding_refined,oats_last_refined,oats_outcome_count",
            filters={"name": "eq.my_tool"}
        )
        mock_db.query.assert_any_call(
            "tool_outcome_log",
            select="query_embedding,outcome,user_rating",
            filters={"tool_name": "eq.my_tool"}
        )
