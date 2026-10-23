from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.intelligence.endpoint_contract import normalize_model_name, uses_engine_options
from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter


@pytest.mark.parametrize('name', ['deepseek-chat', 'google/gemini-example', 'openai/example-model', 'gemini-example'])
def test_explicit_hosted_api_keeps_entire_model_id(name):
    assert normalize_model_name(name, 'https://gateway.example.test/api/v1') == 'openai/' + name


def test_local_proxy_is_not_treated_as_a_model_engine():
    assert not uses_engine_options('http://localhost:8000/v1')
    assert uses_engine_options('http://localhost:1234/v1')
    assert normalize_model_name('gemini-example') == 'gemini/gemini-example'


def test_deepseek_thinking_switch_is_endpoint_specific():
    from src.core.intelligence.endpoint_contract import deepseek_thinking_options
    assert deepseek_thinking_options('https://api.deepseek.com/v1', False) == {'thinking': {'type': 'disabled'}}
    assert deepseek_thinking_options('http://localhost:8000/v1', False) == {}
    assert deepseek_thinking_options('https://api.deepseek.com.evil.test/v1', False) == {}
    assert deepseek_thinking_options('https://api.deepseek.com/v1', None) == {}


@pytest.mark.asyncio
async def test_hosted_request_omits_local_runtime_settings_and_preserves_history():
    captured = {}
    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='Hello', tool_calls=None, reasoning_content=None), finish_reason='stop')], usage=None)
    async def completion(**kwargs):
        captured.update(kwargs)
        return chunks()
    adapter = LiteLLMAdapter(model_name='deepseek-chat', base_url='https://api.example.test/v1', api_key='fictional-key')
    with patch('litellm.acompletion', side_effect=completion):
        result = await adapter.generate([{'role': 'user', 'content': 'Hello'}], thinking_enabled=False)
    assert result.text == 'Hello'
    extra = captured.get('extra_body', {})
    assert 'keep_alive' not in extra and 'chat_template_kwargs' not in extra
    assert 'thinking_budget' not in extra and 'reasoning_effort' not in captured
    assert captured['model'] == 'openai/deepseek-chat'
