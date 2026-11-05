from unittest.mock import AsyncMock

import pytest

from src.core.intelligence.local_model_discovery import LocalModelDiscovery


@pytest.mark.asyncio
async def test_heuristic_never_becomes_verified_on_second_listing(monkeypatch):
    monkeypatch.setattr(LocalModelDiscovery, '_load_capability_cache', lambda self: None)
    monkeypatch.setattr(LocalModelDiscovery, '_save_capability_cache', lambda self: pytest.fail('heuristics must not be persisted as verification'))
    monkeypatch.setattr('src.core.intelligence.adapters.openai_adapter.OpenAIAdapter.discover_models', AsyncMock(return_value=['qwen3-fictional']))
    discovery = LocalModelDiscovery()
    first = await discovery.discover_lmstudio()
    second = await discovery.discover_lmstudio()
    assert not first[0].tools_verified and not second[0].tools_verified
    assert discovery._capability_cache == {}


def test_same_name_on_different_servers_has_different_identity():
    assert LocalModelDiscovery._capability_key('http://localhost:1234/v1', 'example') != LocalModelDiscovery._capability_key('http://localhost:11434/v1', 'example')
