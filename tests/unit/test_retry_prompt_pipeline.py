"""
Unit tests for retry prompt pipeline behavior.

Verifies that when a prompt is retried:
1. The semantic response cache is bypassed and cleared for the session.
2. Conversation history in the database is truncated after keep_count messages.
3. LangGraph checkpoints are cleared so prior attempts are not retained in graph memory.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.core.orchestration.turn.pipeline import TurnPipeline
from src.core.orchestration.turn.scratch import TurnScratch
from src.shared.context import RequestContext


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.default_adapter = MagicMock()
    coordinator.default_adapter.model_name = "test-model"
    coordinator._semantic_cache = MagicMock()
    coordinator._semantic_cache.lookup.return_value = "cached response"
    coordinator._semantic_cache_eligible.return_value = True
    coordinator._complete_from_semantic_cache = MagicMock(return_value="cached response")
    coordinator._emitter = MagicMock()
    coordinator.event_bus = MagicMock()
    coordinator._langgraph_engine = MagicMock()
    coordinator._langgraph_engine.get_history = AsyncMock(return_value=[{"role": "user", "content": "old"}])
    coordinator._langgraph_engine.clear_checkpoints = AsyncMock()
    coordinator._history_loader = MagicMock()
    coordinator._history_loader.load = AsyncMock(return_value=[{"role": "user", "content": "kept"}])
    return coordinator


def test_retry_bypasses_and_clears_semantic_cache(mock_coordinator):
    ctx = RequestContext(
        platform="gui",
        session_id="session-123",
        retry=True,
        keep_count=2,
    )
    pipeline = TurnPipeline(
        coordinator=mock_coordinator,
        scratch=TurnScratch(),
        prompt="Tell me a joke",
        context=ctx,
        category="chat",
    )

    # _prepare should return None (not cached response) and clear cache
    res = pipeline._prepare()
    assert res is None
    assert pipeline.cache_eligible is False
    assert pipeline.is_retry is True
    mock_coordinator._semantic_cache.clear.assert_called_once_with("session-123")
    mock_coordinator._semantic_cache.lookup.assert_not_called()


@pytest.mark.asyncio
async def test_retry_clears_checkpoints_in_prepare_graph(mock_coordinator):
    ctx = RequestContext(
        platform="gui",
        session_id="session-123",
        retry=True,
        keep_count=1,
    )
    pipeline = TurnPipeline(
        coordinator=mock_coordinator,
        scratch=TurnScratch(),
        prompt="Tell me a joke",
        context=ctx,
        category="chat",
    )
    pipeline._prepare()

    mock_consolidator = MagicMock()
    mock_consolidator.preflight = AsyncMock(return_value=[])
    mock_coordinator._consolidator = mock_consolidator
    mock_coordinator._langgraph_engine.adapter = pipeline.active_adapter
    # When checkpoints are cleared, subsequent check returns empty
    mock_coordinator._langgraph_engine.get_history.return_value = []

    await pipeline._prepare_graph()

    mock_coordinator._langgraph_engine.clear_checkpoints.assert_awaited_once_with("session-123")
    assert pipeline.checkpoints_exist is False


@pytest.mark.asyncio
async def test_retry_truncates_history_before_load(mock_coordinator):
    ctx = RequestContext(
        platform="gui",
        session_id="session-123",
        retry=True,
        keep_count=2,
    )
    pipeline = TurnPipeline(
        coordinator=mock_coordinator,
        scratch=TurnScratch(),
        prompt="Tell me a joke",
        context=ctx,
        category="chat",
    )
    pipeline._prepare()

    mock_repo_instance = MagicMock()
    mock_repo_instance.truncate_conversation_after = AsyncMock(return_value=True)

    with patch(
        "src.infrastructure.repositories.chat_history_repository.ChatHistoryRepository",
        return_value=mock_repo_instance,
    ):
        mock_coordinator._mode_resolver.resolve = AsyncMock(return_value=("chat", "auto_router", 0.9))
        mock_coordinator._mode_resolver.category_for_turn.return_value = "chat"
        mock_coordinator.mcp_ready_event = None

        await pipeline._resolve_mode()

        mock_repo_instance.truncate_conversation_after.assert_awaited_once_with("session-123", 2)
        mock_coordinator._history_loader.load.assert_awaited_once_with("session-123")
