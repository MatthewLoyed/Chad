"""
Regression test: LM Studio / litellm send the `usage` object (including
completion_tokens_details.reasoning_tokens) in a trailing chunk that arrives
AFTER the finish_reason chunk. The streaming loop must not stop reading before
that trailing chunk arrives, or usage/reasoning_tokens is silently dropped.

Confirmed live against a running LM Studio instance (openai/gpt-oss-20b) before
the fix: GenerateResponse.usage came back None on every hybrid-streaming turn.
After the fix, usage.reasoning_tokens is populated as expected.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter


def _delta(content=None, reasoning_content=None):
    return SimpleNamespace(content=content, reasoning_content=reasoning_content, tool_calls=None)


def _chunk(delta=None, finish_reason=None, usage=None):
    choices = [SimpleNamespace(delta=delta, finish_reason=finish_reason)] if delta is not None else []
    return SimpleNamespace(choices=choices, usage=usage)


def _usage_obj(prompt_tokens=91, completion_tokens=291, total_tokens=382, reasoning_tokens=167):
    details = SimpleNamespace(reasoning_tokens=reasoning_tokens)
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        completion_tokens_details=details,
    )


class _FakeStream:
    """Mimics litellm's async chunk iterator: content chunks, then a
    finish_reason chunk, then a *separate* trailing usage-only chunk —
    exactly what LM Studio/gpt-oss-20b sends in practice."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(
        "src.core.intelligence.providers.LMStudioLifecycle.resolve_model_id",
        AsyncMock(return_value="openai/gpt-oss-20b"),
    )
    return LiteLLMAdapter(
        model_name="openai/gpt-oss-20b",
        base_url="http://localhost:1234/v1",
    )


@pytest.mark.asyncio
async def test_usage_captured_when_trailing_after_finish_reason(adapter):
    chunks = [
        _chunk(delta=_delta(reasoning_content="thinking...")),
        _chunk(delta=_delta(content="42")),
        _chunk(delta=_delta(content=""), finish_reason="stop"),
        # Trailing usage-only chunk — arrives one tick after finish_reason, real LM Studio behavior.
        _chunk(delta=_delta(content=""), finish_reason=None, usage=_usage_obj()),
    ]

    async def fake_acompletion(**kwargs):
        return _FakeStream(chunks)

    with patch("litellm.acompletion", side_effect=fake_acompletion):
        resp = await adapter.generate([{"role": "user", "content": "what is 6*7?"}])

    assert resp.finish_reason == "stop"
    assert resp.usage is not None, "usage was dropped — trailing usage chunk was never read"
    assert resp.usage.prompt_tokens == 91
    assert resp.usage.completion_tokens == 291
    assert resp.usage.total_tokens == 382
    assert resp.usage.reasoning_tokens == 167


@pytest.mark.asyncio
async def test_usage_still_captured_when_bundled_with_finish_reason(adapter):
    """Some providers put usage on the same chunk as finish_reason — must keep working."""
    chunks = [
        _chunk(delta=_delta(content="pong"), finish_reason="stop", usage=_usage_obj(reasoning_tokens=None)),
    ]

    async def fake_acompletion(**kwargs):
        return _FakeStream(chunks)

    with patch("litellm.acompletion", side_effect=fake_acompletion):
        resp = await adapter.generate([{"role": "user", "content": "ping"}])

    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 91
    assert resp.usage.reasoning_tokens is None


@pytest.mark.asyncio
async def test_stream_ends_without_usage_does_not_hang(adapter):
    """Providers that never send stream_options usage must not cause an infinite wait."""
    chunks = [
        _chunk(delta=_delta(content="hi"), finish_reason="stop"),
    ]

    async def fake_acompletion(**kwargs):
        return _FakeStream(chunks)

    with patch("litellm.acompletion", side_effect=fake_acompletion):
        resp = await adapter.generate([{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "stop"
    assert resp.usage is None


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint_url,key,expected', [
    ('http://localhost:8000/v1', None, 'not-required'),
    ('http://localhost:8000/v1', 'explicit-test-key', 'explicit-test-key'),
    (None, None, None),
])
async def test_request_credentials_for_optional_custom_endpoint(endpoint_url, key, expected):
    captured = {}
    async def completion(**kwargs):
        captured.update(kwargs)
        return _FakeStream([_chunk(delta=_delta(content='Hello'), finish_reason='stop')])

    adapter = LiteLLMAdapter(model_name='openai/test-model', base_url=endpoint_url, api_key=key)
    with patch('litellm.acompletion', side_effect=completion):
        response = await adapter.generate([{'role': 'user', 'content': 'Hello'}])
    assert response.finish_reason == 'stop'
    assert captured.get('api_key') == expected
