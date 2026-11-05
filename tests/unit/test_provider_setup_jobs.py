import hashlib
import json
import threading

import httpx

from src.infrastructure.services import ollama_setup, provider_benchmark, setup_speed_history


def test_ollama_manifest_checked_before_pull_and_verified_after(monkeypatch):
    body = b'{"schemaVersion":2}'
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(ollama_setup, "catalog", lambda: [{"model": "fictional:4b", "digest": digest}])
    seen = []
    def handler(request):
        seen.append(request.url.path)
        if "/manifests/" in request.url.path:
            return httpx.Response(200, content=body)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fictional:4b", "digest": digest}]})
        return httpx.Response(200, content=b'{"status":"success"}\n')
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ollama_setup.httpx, "Client", lambda **kwargs: client)
    result = ollama_setup.pull({"model_id": "fictional:4b", "base_url": "http://localhost:11434/v1"}, threading.Event(), lambda **kw: None)
    assert result["success"] and seen[-1] == "/api/tags"


def test_changed_registry_manifest_never_starts_pull(monkeypatch):
    monkeypatch.setattr(ollama_setup, "catalog", lambda: [{"model": "fictional:4b", "digest": "sha256:old"}])
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request.method) or httpx.Response(200, content=b'changed')))
    monkeypatch.setattr(ollama_setup.httpx, "Client", lambda **kw: client)
    assert not ollama_setup.pull({"model_id": "fictional:4b", "base_url": "http://localhost:11434/v1"}, threading.Event(), lambda **kw: None)["success"]
    assert calls == ["GET"]


def test_ollama_decode_speed_uses_native_duration(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "done": True, "eval_count": 100, "eval_duration": 2_000_000_000})))
    monkeypatch.setattr(provider_benchmark.httpx, "Client", lambda **kw: client)
    result = provider_benchmark.benchmark({"provider": "ollama", "base_url": "http://localhost:11434/v1", "model_id": "fictional:4b"})
    assert result["stats"]["tokens_per_second"] == 50
    assert result["metric"] == "provider_decode"


def test_speed_test_cannot_contact_hosted_provider():
    result = provider_benchmark.benchmark({"provider": "custom", "base_url": "https://api.example.test/v1", "model_id": "chat"})
    assert not result["success"]


def test_history_does_not_reuse_changed_settings_or_write_secrets(monkeypatch, tmp_path):
    fingerprint = ["settings-v1"]
    monkeypatch.setattr(setup_speed_history, "signature", lambda options: fingerprint[0])
    monkeypatch.setattr(setup_speed_history, "_path", lambda: tmp_path / "history.json")
    options = {"api_key": "fictional-secret", "base_url": "http://localhost:11434/v1"}
    result = {"success": True, "kind": "measured", "metric": "provider_decode", "stats": {"tokens_per_second": 30}}
    setup_speed_history.remember(options, result)
    assert setup_speed_history.previous(options)["stats"]["tokens_per_second"] == 30
    fingerprint[0] = "settings-v2"
    assert setup_speed_history.previous(options) is None
    assert "fictional-secret" not in (tmp_path / "history.json").read_text()
