import sys
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.session_manager import SessionManager
from src.core.orchestration.session_coordinator import SessionCoordinator
from src.core.orchestration.voice_session import VoiceSession
from src.core.orchestration.text_session import TextSession
from src.shared.exceptions import VoiceAssistantError

@pytest.fixture
def mock_config():
    return {
        'audio': {
            'sample_rate': 16000
        }
    }

@pytest.fixture
def mock_dependencies():
    return {
        'model_router': MagicMock(),
        'tool_registry': MagicMock(),
        'memory_engine': MagicMock(),
        'tool_executor': MagicMock(),
        'event_bus': MagicMock()
    }

def test_session_manager_init_without_audio(mock_config, mock_dependencies):
    """
    Assert that SessionManager can be initialized with audio_handler=None.
    Verify that voice_coordinator and watchdog_coordinator are None,
    but SessionCoordinator is successfully instantiated.
    """
    sm = SessionManager(
        config=mock_config,
        model_router=mock_dependencies['model_router'],
        tool_registry=mock_dependencies['tool_registry'],
        audio_handler=None,  # No audio handler
        memory_engine=mock_dependencies['memory_engine'],
        wake_word_detector=None,
        tool_executor=mock_dependencies['tool_executor'],
        event_bus=mock_dependencies['event_bus']
    )

    assert sm.audio is None
    assert sm.voice_coordinator is None
    assert sm.watchdog_coordinator is None
    assert sm.overlay is None
    
    assert isinstance(sm.session_coordinator, SessionCoordinator)
    assert sm.session_coordinator.audio is None

@pytest.mark.asyncio
async def test_start_voice_session_without_audio_raises_error(mock_config, mock_dependencies):
    """
    Assert that starting a voice session when audio_handler is None raises a VoiceAssistantError.
    """
    sm = SessionManager(
        config=mock_config,
        model_router=mock_dependencies['model_router'],
        tool_registry=mock_dependencies['tool_registry'],
        audio_handler=None,
        memory_engine=mock_dependencies['memory_engine'],
        event_bus=mock_dependencies['event_bus']
    )
    
    with pytest.raises(VoiceAssistantError, match="Audio hardware is not initialized"):
        await sm.start_conversation()

@pytest.mark.asyncio
async def test_execute_text_command_creates_text_session(mock_config, mock_dependencies):
    """
    Assert that execute_text_command creates a TextSession and routes command
    to ConversationOrchestrator.process_request.
    """
    sm = SessionManager(
        config=mock_config,
        model_router=mock_dependencies['model_router'],
        tool_registry=mock_dependencies['tool_registry'],
        audio_handler=None,
        memory_engine=mock_dependencies['memory_engine'],
        event_bus=mock_dependencies['event_bus']
    )
    
    # Mock ConversationOrchestrator.process_request
    from unittest.mock import AsyncMock
    orchestrator = sm.conversation_orchestrator
    orchestrator.process_request = AsyncMock(return_value="Mock response text")
    
    # Run the text command
    result = await sm.execute_text_command("Hello Chad", category="chat")
    
    # Verify TextSession was started and process_request was called
    assert isinstance(sm.session_coordinator.active_session, TextSession)
    assert sm.session_coordinator.active_session.is_active is True
    
    orchestrator.process_request.assert_called_once_with(
        prompt="Hello Chad",
        category="chat"
    )
    
    assert result == {"success": True, "response": "Mock response text"}
