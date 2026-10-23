from unittest.mock import MagicMock

from src.infrastructure.services import hardware_setup
from src.shared.config_manager import ConfigManager


def test_manual_overrides_replace_hardware_and_reject_nonfinite(monkeypatch):
    monkeypatch.setattr(hardware_setup, 'assess_hardware', lambda _: {
        'ram_gb': 32, 'available_ram_gb': 20, 'storage_free_gb': 100,
        'gpus': [{'name': 'Detected', 'vram_gb': 24}], 'warnings': [],
    })
    result = hardware_setup.assess('', 8192, {'ram_gb': 16, 'vram_gb': 4})
    assert result['hardware']['gpus'][0]['vram_gb'] == 4
    assert result['hardware']['ram_gb'] == 16
    assert result['hardware']['available_ram_gb'] is None
    assert not hardware_setup.assess('', 8192, {'vram_gb': 'nan'})['success']


def test_shared_specialists_respect_explicit_overrides(monkeypatch):
    from src.core.orchestration.agents.factory import get_agent_model_override
    monkeypatch.setattr(ConfigManager, '_loaded', True)
    monkeypatch.setattr(ConfigManager, '_config', {'setup': {'share_main_model': True},
        'subagent_model_overrides': {'code_agent': {'model_name': 'example-specialist'}}})
    assert get_agent_model_override('research_agent') is None
    assert get_agent_model_override('code_agent')['model_name'] == 'example-specialist'


def test_desktop_assessment_never_relays_hardware_to_brain(monkeypatch):
    from src.clients.desktop.api.setup import SetupAPIMixin
    bridge = SetupAPIMixin()
    bridge._execute_command = MagicMock(side_effect=AssertionError('Hardware must remain local'))
    monkeypatch.setattr(hardware_setup, 'assess', lambda *args: {'success': True})
    assert bridge.assess_model_hardware()['success']
    bridge._execute_command.assert_not_called()


def test_selector_uses_main_endpoint_and_its_credential(monkeypatch):
    from src.infrastructure.tools import registry
    from src.infrastructure.tools.selection.llm_selector import _selector_request_headers
    from types import SimpleNamespace
    monkeypatch.setattr(ConfigManager, '_loaded', True)
    monkeypatch.setattr(ConfigManager, '_config', {'setup': {'share_main_model': True},
        'tools': {'llm_selector_model': 'example-chat'}, 'local_models': {'use_local': True,
        'active_model': {'name': 'example-chat', 'provider': 'static', 'base_url': 'http://localhost:8000/v1', 'api_key': 'fictional-key'}}})
    monkeypatch.setattr(registry, 'LLMToolSelector', lambda **kwargs: kwargs)
    assert registry._build_llm_selector()['base_url'] == 'http://localhost:8000/v1'
    caps = SimpleNamespace(default_api_key=None)
    assert _selector_request_headers('http://localhost:8000/v1', caps)['Authorization'] == 'Bearer fictional-key'
    assert 'Authorization' not in _selector_request_headers('http://localhost:1234/v1', caps)


def test_existing_registry_refreshes_selector_after_setup(monkeypatch):
    from types import SimpleNamespace
    from src.infrastructure.tools import registry
    from src.infrastructure.tools.selection.llm_selector import shared_setup_selector
    monkeypatch.setattr(ConfigManager, '_loaded', True)
    monkeypatch.setattr(ConfigManager, '_config', {'setup': {'share_main_model': True},
        'tools': {'llm_selector_model': 'example-new'}, 'local_models': {
        'active_model': {'name': 'example-new', 'provider': 'lmstudio', 'base_url': 'http://localhost:1234/v1'}}})
    fresh = object()
    monkeypatch.setattr(registry, '_build_llm_selector', lambda: fresh)
    tools = SimpleNamespace(_llm_selector=SimpleNamespace(_model_id='old', _base_url='http://localhost:1234/v1'))
    assert shared_setup_selector(tools) is fresh
    assert tools._llm_selector is fresh


def test_shared_selector_follows_later_main_model_changes(monkeypatch):
    monkeypatch.setattr(ConfigManager, '_loaded', True)
    monkeypatch.setattr(ConfigManager, '_config', {'setup': {'share_main_model': True},
        'tools': {'llm_selector_model': 'old-main'}, 'local_models': {'use_local': True,
        'supervisor': {'name': 'new-main'}}})
    assert ConfigManager.get_llm_selector_model() == 'new-main'
    ConfigManager._config['setup']['share_main_model'] = False
    assert ConfigManager.get_llm_selector_model() == 'old-main'


def test_uncached_embeddings_cannot_download_without_opt_in(monkeypatch):
    import sys
    from types import SimpleNamespace
    from src.core.intelligence.local_embeddings import LocalEmbeddingModel
    import pytest
    def transformer(*args, **kwargs):
        assert kwargs['local_files_only'] is True
        raise OSError('No fictional embedding cache')
    monkeypatch.setattr(ConfigManager, '_loaded', True)
    monkeypatch.setattr(ConfigManager, '_config', {})
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=transformer))
    with pytest.raises(OSError, match='No fictional embedding cache'):
        LocalEmbeddingModel('example/embedding').ensure_local_transformer()
