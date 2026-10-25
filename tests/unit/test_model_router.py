import sys
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.model_router import ModelRouter


def test_downloaded_model_does_not_bypass_residency_coordinator(monkeypatch):
    import io
    import json
    from src.shared.config_manager import ConfigManager
    from src.shared.exceptions import ModelError
    from src.core.intelligence import model_lifecycle

    monkeypatch.setattr(ModelRouter, '_trigger_warmup', lambda self: None)
    monkeypatch.setattr(ConfigManager, 'get_local_model_config', lambda *args: {
        'name': 'example/main', 'provider': 'lmstudio',
        'base_url': 'http://localhost:1234/v1', 'context_length': 8192,
    })
    # OpenAI listing includes DOWNLOADED models even when none are resident.
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs:
                        io.BytesIO(json.dumps({'data': [{'id': 'example/main'}]}).encode()))
    ensure = MagicMock(return_value=False)
    monkeypatch.setattr(model_lifecycle, 'ensure_resident_sync', ensure)
    with patch('models.litellm_model.LiteLLMModel') as model:
        with pytest.raises(ModelError):
            ModelRouter({}, MagicMock())._ensure_local_model('chat')
        model.assert_not_called()
    ensure.assert_called_once_with(
        'http://localhost:1234/v1', 'example/main', owner=model_lifecycle.OWNER_MAIN_MODEL,
        priority=model_lifecycle.current_priority(), context_length=8192,
    )

@pytest.fixture
def mock_config():
    return {
        'models': {
            'chat': {
                'type': 'gemini_live',
                'model': 'gemini-2.5-flash-native-audio-preview',
            },
            'text': {
                'type': 'gemini_text',
                'model': 'gemini-2.0-flash',
            },
            'agent': {
                'type': 'litellm_model',
                'model': 'gemini-2.0-flash',
            },
            'router': {
                'type': 'gemini_text',
                'model': 'gemini-2.0-flash-lite',
            },
            'background': {
                'type': 'gemini_text',
                'model': 'gemini-2.0-flash-lite',
            }
        }
    }

@pytest.fixture
def mock_tool_registry():
    return MagicMock()

@patch('src.shared.config_manager.ConfigManager.is_local_model_active')
@patch('src.shared.config_manager.ConfigManager.get_active_model')
def test_cloud_model_override_interactive_categories(mock_get_active_model, mock_is_local_model_active, mock_config, mock_tool_registry):
    """
    Assert that when local model is NOT active, the dropdown cloud model selection
    correctly overrides the active LLM inside all interactive harnesses ('chat', 'text', and 'agent').
    """
    mock_is_local_model_active.return_value = False
    mock_get_active_model.return_value = 'claude-3-5-sonnet-latest'
    
    # We mock _create_model_instance to return a dummy BaseModel subclass
    # so we don't try to instantiate real LangChain/LiteLLM models in unit tests.
    router = ModelRouter(mock_config, mock_tool_registry)
    
    # Mock _create_model_instance to return a dummy model with model_name set
    def mock_create_instance(model_type, config):
        m = MagicMock()
        m.model_name = config.get('model')
        return m
        
    router._create_model_instance = mock_create_instance
    
    # Assert 'chat' category is overridden
    chat_model = router.route('chat')
    assert chat_model.model_name == 'claude-3-5-sonnet-latest'
    
    # Assert 'text' category is overridden
    text_model = router.route('text')
    assert text_model.model_name == 'claude-3-5-sonnet-latest'
    
    # Assert 'agent' category is overridden (THE BUG FIX ASSERTION)
    agent_model = router.route('agent')
    assert agent_model.model_name == 'claude-3-5-sonnet-latest'

@patch('asyncio.create_task')
@patch('src.shared.config_manager.ConfigManager.is_local_model_active')
def test_local_model_override_interactive_categories(mock_is_local_model_active, mock_create_task, mock_config, mock_tool_registry):
    """
    Assert that when local model IS active, all interactive harnesses ('chat', 'text', and 'agent')
    are overridden to the local model.
    """
    mock_is_local_model_active.return_value = True

    # ModelRouter.__init__ fires a predictive-warmup asyncio.create_task() when
    # a local model is active and configured — this is a plain sync test with
    # no running event loop, so that call is mocked out here rather than left
    # to only work by accident whenever a local .env happens to set UPLINK=1
    # (which short-circuits warmup before reaching create_task) — see the real
    # repo config.yaml's local_models.active_model, which is set for real (#216).
    router = ModelRouter(mock_config, mock_tool_registry)
    
    # Mock _ensure_local_model to return a dummy model
    dummy_local = MagicMock()
    dummy_local.model_name = 'local-llama-3'
    router._ensure_local_model = MagicMock(return_value=dummy_local)
    
    # Assert 'chat', 'text', and 'agent' all route to local model
    assert router.route('chat').model_name == 'local-llama-3'
    assert router.route('text').model_name == 'local-llama-3'
    assert router.route('agent').model_name == 'local-llama-3'

@patch('src.shared.config_manager.ConfigManager.is_local_model_active')
@patch('src.shared.config_manager.ConfigManager.get_active_model')
def test_non_interactive_categories_are_not_overridden(mock_get_active_model, mock_is_local_model_active, mock_config, mock_tool_registry):
    """
    Assert that non-interactive categories (e.g. 'background') are NOT overridden by dropdown selections.
    """
    mock_is_local_model_active.return_value = False
    mock_get_active_model.return_value = 'claude-3-5-sonnet-latest'
    
    router = ModelRouter(mock_config, mock_tool_registry)
    
    # Mock _create_model_instance to return original model name
    def mock_create_instance(model_type, config):
        m = MagicMock()
        m.model_name = config.get('model')
        return m
    router._create_model_instance = mock_create_instance
    
    # Assert 'background' category keeps its original model name
    background_model = router.route('background')
    assert background_model.model_name == 'gemini-2.0-flash-lite'

