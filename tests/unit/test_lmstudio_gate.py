"""Unit tests for the lmstudio-gate cross-process priority queue (issue #478, #450).

Covers the parts that are actually tricky and must not regress:
  • priority ordering — a higher-priority waiter jumps ahead of lower-priority ones
    that are already waiting, FIFO holds within a tier, and an in-flight request is
    never preempted;
  • cancellation — a waiter that leaves the queue (client disconnect) doesn't wedge
    the front;
  • tier resolution from header / body;
  • the proxy forwards inference bodies to the upstream, strips our internal
    X-Chad-Priority header, and passes management calls straight through;
  • vendor-agnostic upstream resolution (#450) — the gate follows whichever local
    backend is currently active (LM Studio, Ollama, custom endpoint), not hardcoded
    to LM Studio, and gate_client only routes traffic that matches that upstream.
"""

import asyncio
from unittest.mock import patch

import httpx
import pytest

from src.infrastructure.llm import gate_client
from src.infrastructure.llm import lmstudio_gate as gate_mod
from src.infrastructure.llm.lmstudio_gate import PriorityGate, _resolve_tier, app


def test_alpha_configuration_uses_direct_inference_without_a_sidecar(monkeypatch):
    from pathlib import Path
    import yaml

    config = yaml.safe_load((Path(__file__).resolve().parents[2] / 'config/config.yaml').read_text(encoding='utf-8'))
    monkeypatch.delenv('CHAD_LMSTUDIO_GATE_ENABLED', raising=False)
    monkeypatch.setattr(gate_client.ConfigManager, 'get', lambda key, default=None:
                        config['local_models']['gate']['enabled'] if key == 'local_models.gate.enabled' else default)
    with patch.object(gate_client.subprocess, 'Popen') as spawn:
        assert gate_client.ensure_gate_running() is False
        assert gate_client.inference_base_url('http://localhost:8000/v1') == 'http://localhost:8000/v1'
        spawn.assert_not_called()


def test_resolve_tier_from_header_and_body():
    assert _resolve_tier("interactive", None) == 0
    assert _resolve_tier("background", None) == 10
    assert _resolve_tier("EVAL", None) == 20
    assert _resolve_tier("autonomous", None) == 20
    # body fallback (stock OpenAI clients)
    assert _resolve_tier(None, {"priority": "background"}) == 10
    assert _resolve_tier(None, {"priority": True}) == 0
    assert _resolve_tier(None, {"priority": False}) == 10
    # defaults / garbage → interactive
    assert _resolve_tier(None, None) == 0
    assert _resolve_tier("nonsense", None) == 0
    # header wins over body
    assert _resolve_tier("interactive", {"priority": "background"}) == 0


async def test_priority_gate_orders_by_priority_then_fifo():
    gate = PriorityGate(max_concurrent=1)
    admitted = []
    releases = {}

    async def worker(name, tier):
        async with gate.slot(tier):
            admitted.append(name)
            await releases[name].wait()

    # Occupy the single slot so everyone else must queue.
    blocker_release = asyncio.Event()

    async def blocker():
        async with gate.slot(0):
            admitted.append("blocker")
            await blocker_release.wait()

    bt = asyncio.create_task(blocker())
    await asyncio.sleep(0.05)
    assert admitted == ["blocker"]

    # Arrival order A(background) → B(interactive) → C(background).
    for n in ("A", "B", "C"):
        releases[n] = asyncio.Event()
    ta = asyncio.create_task(worker("A", 10)); await asyncio.sleep(0.02)
    tb = asyncio.create_task(worker("B", 0)); await asyncio.sleep(0.02)
    tc = asyncio.create_task(worker("C", 10)); await asyncio.sleep(0.02)

    # Free the slot: B (higher priority) jumps ahead of A even though A arrived first.
    blocker_release.set()
    await asyncio.sleep(0.05)
    assert admitted == ["blocker", "B"]

    releases["B"].set(); await asyncio.sleep(0.05)
    assert admitted == ["blocker", "B", "A"]      # FIFO within the background tier

    releases["A"].set(); await asyncio.sleep(0.05)
    assert admitted == ["blocker", "B", "A", "C"]

    releases["C"].set()
    await asyncio.gather(bt, ta, tb, tc)


async def test_priority_gate_never_exceeds_max_concurrent():
    gate = PriorityGate(max_concurrent=1)
    peak = 0
    current = 0

    async def worker():
        nonlocal peak, current
        async with gate.slot(0):
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.02)
            current -= 1

    await asyncio.gather(*[worker() for _ in range(6)])
    assert peak == 1


async def test_priority_gate_cancel_does_not_wedge_front():
    gate = PriorityGate(max_concurrent=1)
    admitted = []
    blocker_release = asyncio.Event()

    async def blocker():
        async with gate.slot(0):
            await blocker_release.wait()

    bt = asyncio.create_task(blocker())
    await asyncio.sleep(0.05)

    # A high-priority waiter that we cancel before it's ever admitted.
    async def doomed():
        async with gate.slot(0):
            admitted.append("doomed")

    dt = asyncio.create_task(doomed())
    await asyncio.sleep(0.02)

    good_release = asyncio.Event()

    async def good():
        async with gate.slot(0):
            admitted.append("good")
            await good_release.wait()

    gt = asyncio.create_task(good())
    await asyncio.sleep(0.02)

    dt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dt

    # Releasing the blocker must admit `good` — the cancelled waiter must not block it.
    blocker_release.set()
    await asyncio.sleep(0.05)
    assert admitted == ["good"]
    good_release.set()
    await asyncio.gather(bt, gt)


async def _drive(app_, method, path, headers=None, content=None):
    transport = httpx.ASGITransport(app=app_)
    async with httpx.AsyncClient(transport=transport, base_url="http://gate") as client:
        return await client.request(method, path, headers=headers or {}, content=content)


async def test_inference_forwards_body_and_strips_priority_header(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    # Force the gate's upstream client to a mock transport instead of a real LM Studio.
    monkeypatch.setattr(gate_mod, "_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # Deterministic upstream URL regardless of machine config.
    monkeypatch.setattr(gate_mod, "_upstream_base_url", lambda: "http://localhost:1234/v1")

    body = b'{"model":"m","messages":[],"stream":false}'
    resp = await _drive(app, "POST", "/v1/chat/completions",
                        headers={"X-Chad-Priority": "background",
                                 "Content-Type": "application/json"},
                        content=body)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert captured["path"] == "/v1/chat/completions"
    assert captured["body"] == body                    # body forwarded unchanged
    assert "x-chad-priority" not in captured["headers"]  # internal header stripped


async def test_management_call_passes_through(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"data": [{"id": "model-a"}]})

    monkeypatch.setattr(gate_mod, "_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(gate_mod, "_upstream_base_url", lambda: "http://localhost:1234/v1")

    resp = await _drive(app, "GET", "/v1/models")
    assert resp.status_code == 200
    assert seen["path"] == "/v1/models"
    assert resp.json()["data"][0]["id"] == "model-a"


# ---------------------------------------------------------------------------
# Vendor-agnostic upstream resolution (#450) — the gate must follow whichever
# local backend is active, not hardcode LM Studio.
# ---------------------------------------------------------------------------

def test_upstream_defaults_to_lmstudio_when_no_active_model(monkeypatch):
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get_local_model_config", lambda: {}
    )
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        lambda key, default=None: default,
    )
    assert gate_mod._upstream_base_url() == "http://localhost:1234/v1"


def test_upstream_follows_active_ollama_model(monkeypatch):
    active = {"name": "qwen2.5", "provider": "ollama", "base_url": "http://localhost:11434/v1"}
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get_local_model_config", lambda: active
    )
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        lambda key, default=None: default,
    )
    assert gate_mod._upstream_base_url() == "http://localhost:11434/v1"


def test_explicit_upstream_override_wins_over_active_model(monkeypatch):
    active = {"name": "qwen2.5", "provider": "ollama", "base_url": "http://localhost:11434/v1"}
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get_local_model_config", lambda: active
    )

    def _get(key, default=None):
        if key == "local_models.gate.upstream_base_url":
            return "http://my-custom-host:8000/v1"
        return default

    monkeypatch.setattr("src.shared.config_manager.ConfigManager.get", _get)
    assert gate_mod._upstream_base_url() == "http://my-custom-host:8000/v1"


def test_is_gateable_url_matches_only_the_gates_current_upstream(monkeypatch):
    with patch.object(gate_client, "_active_gate_upstream", return_value="http://localhost:11434/v1"):
        assert gate_client._is_gateable_url("http://localhost:11434/v1") is True
        assert gate_client._is_gateable_url("http://localhost:1234/v1") is False
        assert gate_client._is_gateable_url("") is False
        assert gate_client._is_gateable_url(None) is False


def test_inference_base_url_routes_ollama_when_gate_points_at_it(monkeypatch):
    monkeypatch.setattr(gate_client, "gate_enabled", lambda: True)
    monkeypatch.setattr(gate_client, "gate_alive", lambda timeout=1.0: True)
    monkeypatch.setattr(gate_client, "gate_base_url", lambda: "http://127.0.0.1:1235/v1")
    with patch.object(gate_client, "_active_gate_upstream", return_value="http://localhost:11434/v1"):
        assert gate_client.inference_base_url("http://localhost:11434/v1") == "http://127.0.0.1:1235/v1"
        # A different backend's traffic (e.g. a stray LM Studio call while Ollama is
        # active) must NOT be redirected into a gate pointed at Ollama.
        assert gate_client.inference_base_url("http://localhost:1234/v1") == "http://localhost:1234/v1"
