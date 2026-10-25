from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.services.setup_validation import test_model as validate_model


@pytest.mark.asyncio
async def test_explicit_consent_and_chat_model_required(monkeypatch):
    generate = AsyncMock()
    monkeypatch.setattr('src.core.intelligence.adapters.litellm_adapter.LiteLLMAdapter.generate', generate)
    assert not (await validate_model('https://api.example.test/v1', 'key', 'chat'))['success']
    assert not (await validate_model('https://api.example.test/v1', 'key', 'text-embedding', True))['success']
    assert not (await validate_model('https://user:secret@api.example.test', 'key', 'chat', True))['success']
    generate.assert_not_called()


@pytest.mark.asyncio
async def test_chat_answer_is_not_a_tool_compatibility_claim(monkeypatch):
    generate = AsyncMock(return_value=SimpleNamespace(text='Hello!', finish_reason='stop', tool_calls=[]))
    monkeypatch.setattr('src.core.intelligence.adapters.litellm_adapter.LiteLLMAdapter.generate', generate)
    result = await validate_model('https://api.example.test/v1', 'fictional-key', 'vendor/chat', True)
    assert result['success'] and result['reply'] == 'Hello!'
    assert result['tools_verified'] is False
    kwargs = generate.call_args.kwargs
    assert kwargs['tools'] is None and kwargs['max_tokens'] == 64


@pytest.mark.asyncio
@pytest.mark.parametrize('text,finish,calls', [('', 'stop', []), ('private error', 'error', []), ('', 'tool_calls', [{'id': 'call'}])])
async def test_empty_error_and_unrequested_tools_do_not_complete_setup(monkeypatch, text, finish, calls):
    generate = AsyncMock(return_value=SimpleNamespace(text=text, finish_reason=finish, tool_calls=calls))
    monkeypatch.setattr('src.core.intelligence.adapters.litellm_adapter.LiteLLMAdapter.generate', generate)
    result = await validate_model('http://localhost:8000/v1', '', 'chat', True)
    assert not result['success'] and 'private error' not in str(result)


@pytest.mark.asyncio
async def test_exception_does_not_echo_keys_or_provider_body(monkeypatch):
    generate = AsyncMock(side_effect=RuntimeError('fictional-key private provider response'))
    monkeypatch.setattr('src.core.intelligence.adapters.litellm_adapter.LiteLLMAdapter.generate', generate)
    result = await validate_model('https://api.example.test/v1', 'fictional-key', 'chat', True)
    assert not result['success'] and 'fictional-key' not in str(result)


@pytest.mark.asyncio
async def test_custom_discovery_reports_unknown_tools_and_ignores_non_chat(monkeypatch):
    import httpx
    from src.infrastructure.services.setup_wizard import probe_local_backend
    response = httpx.Response(200, json={'data': [{'id': 'chat'}, {'id': 'embedding'}, {'id': 'whisper'}, {}]}, request=httpx.Request('GET', 'https://api.example.test/models'))
    monkeypatch.setattr(httpx.AsyncClient, 'get', AsyncMock(return_value=response))
    result = await probe_local_backend('custom', 'https://api.example.test', 'fictional-key')
    assert result['reachable'] and [m['id'] for m in result['models']] == ['chat']
    assert result['models'][0]['supports_tools'] is False
    assert result['models'][0]['tools_verified'] is False


@pytest.mark.asyncio
async def test_no_models_endpoint_still_allows_manual_model_entry(monkeypatch):
    import httpx
    from src.infrastructure.services.setup_wizard import probe_local_backend
    response = httpx.Response(404, request=httpx.Request('GET', 'https://api.example.test/models'))
    monkeypatch.setattr(httpx.AsyncClient, 'get', AsyncMock(return_value=response))
    result = await probe_local_backend('custom', 'https://api.example.test')
    assert result['reachable'] and 'model ID' in result['detail']


def test_cloud_choice_preserves_endpoint_and_hosting_without_verifying_tools(monkeypatch):
    from src.infrastructure.services.setup_wizard import save_provider_choice
    from src.shared.config_manager import ConfigManager
    from src.shared.model_selection import describe_endpoint_model
    saved = {}
    repository = SimpleNamespace(load=lambda *args, **kwargs: {},
        save=lambda name, values, **kwargs: saved.update(values) is None)
    monkeypatch.setattr('src.infrastructure.repositories.ConfigRepository', lambda: repository)
    monkeypatch.setattr(ConfigManager, '_config', {})
    assert save_provider_choice('cloud', 'https://gateway.example.test/v1', 'fictional-key', 'vendor/chat', False)['success']
    model = saved['local_models']['active_model']
    assert model['base_url'] == 'https://gateway.example.test/v1' and model['name'] == 'vendor/chat'
    assert model['provider'] == 'static' and model['hosting'] == 'cloud'
    assert describe_endpoint_model(model)['hosting'] == 'cloud'
    assert not model['tools_verified'] and not model['supports_tools']
