from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["lmstudio", "ollama", "custom"])
async def test_empty_server_is_reachable_and_tools_remain_unknown(monkeypatch, provider):
    from src.infrastructure.services.setup_wizard import probe_local_backend
    monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=httpx.Response(
        200, json={"data": []}, request=httpx.Request("GET", "http://localhost:8000/v1/models"))))
    result = await probe_local_backend(provider, "http://localhost:8000/v1")
    assert result["reachable"] and result["models"] == []


@pytest.mark.asyncio
async def test_authentication_is_not_an_empty_server(monkeypatch):
    from src.infrastructure.services.setup_wizard import probe_local_backend
    monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=httpx.Response(
        401, request=httpx.Request("GET", "https://api.example.test/v1/models"))))
    result = await probe_local_backend("ollama", "https://api.example.test/v1", "fictional")
    assert result["reachable"] and result["success"] is False
    assert "API key" in result["detail"]


def test_known_discovery_lists_without_generation():
    from src.clients.desktop.api.setup import SetupAPIMixin
    bridge = SetupAPIMixin()
    calls = []
    def probe(provider, url, key):
        calls.append((provider, url, key))
        return {"reachable": provider == "ollama", "models": []}
    bridge.probe_local_backend = probe
    result = bridge.discover_setup_servers()
    assert result["scope"] == "brain_host"
    assert len(calls) == 2 and all(key == "" for _, _, key in calls)
    assert result["servers"][1]["reachable"]


def test_switch_and_return_preserves_exact_identity_and_user_settings(monkeypatch):
    from src.infrastructure.services.setup_wizard import save_provider_choice
    from src.shared.config_manager import ConfigManager
    saved = {"local_models": {"active_model": {"provider": "static", "name": "vendor/chat",
        "base_url": "https://api.example.test/v1", "api_key": "fictional-a", "context_length": 16384}}}
    repository = SimpleNamespace(load=lambda *a, **kw: saved, save=lambda *a, **kw: True)
    monkeypatch.setattr("src.infrastructure.repositories.ConfigRepository", lambda: repository)
    monkeypatch.setattr(ConfigManager, "_config", {})
    assert save_provider_choice("ollama", "http://localhost:11434/v1", "fictional-b", "example:4b", False)["success"]
    assert save_provider_choice("cloud", "https://api.example.test/v1", "fictional-a", "vendor/chat", False)["success"]
    restored = saved["local_models"]["active_model"]
    assert restored["context_length"] == 16384 and restored["api_key"] == "fictional-a"
    assert len(saved["local_models"]["connections"]) == 2
