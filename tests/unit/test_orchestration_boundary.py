import sys
from pathlib import Path
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.conversation_orchestrator import ConversationOrchestrator, TurnInitiator
from src.shared.context import RequestContext

@pytest.fixture
def mock_orch_dependencies():
    model_coordinator = MagicMock()
    
    tool_coordinator = MagicMock()
    tool_coordinator.tools = MagicMock()
    tool_coordinator.executor = MagicMock()
    tool_coordinator.reset_turn = MagicMock()
    
    state_coordinator = MagicMock()
    state_coordinator.current_state = "ready"
    state_coordinator.transition_to = AsyncMock()
    
    memory_engine = MagicMock()
    memory_engine.log_turn = MagicMock()
    
    event_bus = MagicMock()
    
    return {
        'model_coordinator': model_coordinator,
        'tool_coordinator': tool_coordinator,
        'state_coordinator': state_coordinator,
        'memory_engine': memory_engine,
        'event_bus': event_bus,
        'config': {}
    }

@pytest.fixture
def orchestrator(mock_orch_dependencies):
    # We patch PromptRegistry.get_instance to avoid loading DB files in unit tests
    mock_registry = MagicMock()
    mock_registry.warmup = AsyncMock()
    
    with patch("src.infrastructure.repositories.prompt_registry.PromptRegistry.get_instance", return_value=mock_registry):
        orch = ConversationOrchestrator(
            model_coordinator=mock_orch_dependencies['model_coordinator'],
            tool_coordinator=mock_orch_dependencies['tool_coordinator'],
            state_coordinator=mock_orch_dependencies['state_coordinator'],
            memory_engine=mock_orch_dependencies['memory_engine'],
            event_bus=mock_orch_dependencies['event_bus'],
            config=mock_orch_dependencies['config']
        )
        return orch

@pytest.mark.asyncio
async def test_process_request_calls_memory_persistence(orchestrator):
    """
    Assert that process_request results in persisting the turn to memory.
    In the pre-refactor state, since TurnCoordinator.dispatch is mocked,
    this test will FAIL because the memory persistence is located in TurnCoordinator.
    Post-refactor, this will PASS.
    """
    orchestrator.model_coord.select_model = AsyncMock()
    mock_model = MagicMock()
    mock_model.model_name = "Mock Model"
    orchestrator.model_coord.select_model.return_value = mock_model
    
    # Mock the turn coordinator to just return a response
    orchestrator.turn_coord.dispatch = AsyncMock(return_value="Mock response text")
    
    context = RequestContext(platform="pc", session_id="test_session")
    
    await orchestrator.process_request(
        prompt="Hello world",
        category="agent",
        initiator=TurnInitiator.USER,
        context=context
    )
    
    # Verify memory persistence occurred
    await asyncio.sleep(0.05)
    assert orchestrator.memory.log_turn.call_count == 2
    orchestrator.memory.log_turn.assert_any_call(role="user", content="Hello world", session_key="test_session")
    orchestrator.memory.log_turn.assert_any_call(role="assistant", content="Mock response text", session_key="test_session")

@pytest.mark.asyncio
async def test_process_request_calls_memory_persistence_with_attachments(orchestrator):
    """
    Verify image attachments are correctly formatted before logging to memory.
    """
    orchestrator.model_coord.select_model = AsyncMock()
    mock_model = MagicMock()
    mock_model.model_name = "Mock Model"
    orchestrator.model_coord.select_model.return_value = mock_model
    orchestrator.turn_coord.dispatch = AsyncMock(return_value="Mock response text")
    
    context = RequestContext(platform="pc", session_id="test_session")
    content_blocks = [{"type": "image", "source": {}}]
    
    await orchestrator.process_request(
        prompt="Hello world",
        category="agent",
        initiator=TurnInitiator.USER,
        context=context,
        content_blocks=content_blocks
    )
    
    await asyncio.sleep(0.05)
    assert orchestrator.memory.log_turn.call_count == 2
    orchestrator.memory.log_turn.assert_any_call(role="user", content="Hello world\n\nAttachments: [Image Attached]", session_key="test_session")
    orchestrator.memory.log_turn.assert_any_call(role="assistant", content="Mock response text", session_key="test_session")
