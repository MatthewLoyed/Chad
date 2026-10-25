from unittest.mock import AsyncMock
import pytest
from src.infrastructure.services import setup_roles as roles
from src.shared.config_manager import ConfigManager
from src.shared.model_selection import selector_config, secondary_config


@pytest.fixture(autouse=True)
def mock_runtime_contract(monkeypatch):
    async def ready(endpoint, model, context, operation):
        return await operation()
    monkeypatch.setattr('src.infrastructure.services.setup_role_runtime.checked_role', ready)


def role_plan():
    return {'secondary': 'fast', 'selector': 'instruct', 'context': 8192,
            'residency': 'sequential', 'runtime_budget_gb': 10, 'estimates': {'main': 7, 'fast': 8, 'instruct': 3}}


@pytest.mark.asyncio
async def test_checks_roles_without_saving_and_rejects_changed_credentials(monkeypatch):
    generate = AsyncMock(return_value={'success': True, 'reply': 'Hello'})
    selector = AsyncMock(return_value=True)
    monkeypatch.setattr(roles, 'test_model', generate)
    monkeypatch.setattr(roles, '_test_selector', selector)
    checked = await roles.validate_roles('http://localhost:1234/v1', 'fictional', 'main', role_plan(), True)
    assert checked['success'] and checked['checks'] == {'primary': True, 'secondary': True, 'selector': True}
    assert generate.await_count == 2 and selector.await_count == 1
    assert not roles.save_checked_bundle('http://localhost:1234/v1', 'changed', 'main', checked['validation_token'])['success']


@pytest.mark.asyncio
async def test_bad_selector_can_only_save_explicit_chat_only_fallback(monkeypatch):
    monkeypatch.setattr(roles, 'test_model', AsyncMock(return_value={'success': True, 'reply': 'Hello'}))
    monkeypatch.setattr(roles, '_test_selector', AsyncMock(return_value=False))
    checked = await roles.validate_roles('http://localhost:1234/v1', '', 'main', role_plan(), True)
    assert not checked['success'] and checked['reply']
    assert not roles.save_checked_bundle('http://localhost:1234/v1', '', 'main', checked['validation_token'])['success']
    saved = {}
    monkeypatch.setattr('src.infrastructure.services.setup_wizard.save_provider_choice', lambda *args, **kwargs: saved.update(kwargs) or {'success': True})
    assert roles.save_checked_bundle('http://localhost:1234/v1', '', 'main', checked['validation_token'], True)['success']
    assert saved['extra_updates']['tools']['selector_model'] is None
    assert saved['extra_updates']['local_models']['secondary_model']['name'] == 'main'


def test_dedicated_selector_keeps_own_identity_and_key_after_main_changes(monkeypatch):
    monkeypatch.setattr(ConfigManager, '_loaded', True)
    monkeypatch.setattr(ConfigManager, '_config', {'setup': {'share_main_model': True},
        'tools': {'selector_model': {'name': 'instruct', 'base_url': 'http://localhost:1234/v1', 'api_key': 'selector-key'}},
        'local_models': {'use_local': True, 'supervisor': {'name': 'cloud-main', 'base_url': 'https://cloud.example.test/v1', 'api_key': 'cloud-key'},
                         'secondary_model': {'name': 'fast'}}})
    assert ConfigManager.get_llm_selector_model() == 'instruct'
    assert selector_config()['api_key'] == 'selector-key'
    assert secondary_config()['name'] == 'fast'
    ConfigManager._config['setup']['secondary_policy'] = 'main'
    assert secondary_config()['name'] == 'cloud-main'


def test_role_updates_budget_context_and_subagent_inheritance_without_pinning_agents():
    update = roles.role_updates(role_plan(), 'main', 'http://localhost:1234/v1', 'fictional')
    assert update['tools']['llm_selector_model'] == 'instruct'
    assert update['tools']['llm_selector_unload_after_select'] is True
    assert update['local_models']['supervisor']['context_length'] == 8192
    assert update['setup']['share_main_model'] is True
    assert 'subagent_model_overrides' not in update


def test_exact_size_estimates_win_over_generic_parameter_count():
    from src.core.intelligence.model_lifecycle import _estimate_size_gb
    assert _estimate_size_gb('prism-ml/bonsai-27b', {'27b': 16, 'prism-ml/bonsai-27b': 6}) == 6


@pytest.mark.asyncio
async def test_invalid_bundle_roles_fail_before_generation(monkeypatch):
    generate = AsyncMock()
    monkeypatch.setattr(roles, 'test_model', generate)
    assert not (await roles.validate_roles('http://localhost:1234/v1', '', 'main', {}, True))['success']
    generate.assert_not_called()
