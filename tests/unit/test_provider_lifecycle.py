"""Unit tests for the provider lifecycle abstraction (#450a).

Covers (a) base_url -> provider resolution, (b) each strategy's load/unload/list_loaded
HTTP contract with a mocked httpx client, and (c) that the LM Studio strategy still speaks
its native ``/api/v1/models/*`` dialect byte-for-byte. No server, no LLM, no VRAM cost.
"""

import json

import pytest

import src.core.intelligence.providers as providers
from src.core.intelligence.providers import (
    LMStudioLifecycle,
    OllamaLifecycle,
    StaticEndpointLifecycle,
    get_provider_for,
    resolve_provider_name,
)


# ---------------------------------------------------------------------------
# A tiny fake httpx.AsyncClient that records requests and replays canned responses.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, calls, routes):
        self._calls = calls
        self._routes = routes  # {("GET"|"POST", url): _FakeResponse}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self._calls.append(("GET", url, kw.get("json")))
        return self._routes.get(("GET", url), _FakeResponse(404, {}))

    async def post(self, url, **kw):
        self._calls.append(("POST", url, kw.get("json")))
        return self._routes.get(("POST", url), _FakeResponse(404, {}))


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    # get_provider_for caches instances per base_url; keep tests isolated.
    providers._instances.clear()
    yield
    providers._instances.clear()


def _patch_httpx(monkeypatch, module, routes):
    # These HTTP-contract tests replace every client with a canned fake. Keep
    # ambient eval model restrictions from hiding the behavior under test.
    monkeypatch.delenv("CHAD_TESTING", raising=False)
    calls = []

    def _factory(*a, **kw):
        return _FakeClient(calls, routes)

    fake = _FakeHttpx(_factory)
    monkeypatch.setattr(module, "httpx", fake)
    # list_loaded()/the OpenAI-compat default live in base.py — patch it too so inherited
    # calls don't fall through to a real server (e.g. a live LM Studio on :1234).
    from src.core.intelligence.providers import base as _base
    monkeypatch.setattr(_base, "httpx", fake)
    return calls


class _FakeHttpx:
    def __init__(self, factory):
        self._factory = factory

    def AsyncClient(self, *a, **kw):  # noqa: N802 — mimic httpx API
        return self._factory(*a, **kw)


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:1234/v1", "lmstudio"),
        ("http://127.0.0.1:1234/v1", "lmstudio"),
        ("http://localhost:11434/v1", "ollama"),
        ("http://my-ollama-host/v1", "ollama"),
        ("http://localhost:8000/v1", "static"),   # vLLM default port
        ("http://localhost:8080/v1", "static"),   # llama.cpp server default
    ],
)
def test_resolve_provider_name_heuristics(monkeypatch, url, expected):
    # No config providers -> pure URL heuristic.
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        staticmethod(lambda *a, **k: {}),
    )
    assert resolve_provider_name(url) == expected


def test_config_declared_provider_overrides_heuristic(monkeypatch):
    # A custom endpoint on a heuristic-ambiguous port, explicitly declared as vLLM/static.
    cfg = {
        "local_models.providers": {
            "myendpoint": {"base_url": "http://localhost:1234/v1", "provider": "vllm"},
        }
    }
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        staticmethod(lambda key, default=None: cfg.get(key, default)),
    )
    # Would be "lmstudio" by port, but config declares it static (vllm alias).
    assert resolve_provider_name("http://localhost:1234/v1") == "vllm"
    assert isinstance(get_provider_for("http://localhost:1234/v1"), StaticEndpointLifecycle)


def test_get_provider_for_returns_correct_types(monkeypatch):
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        staticmethod(lambda *a, **k: {}),
    )
    assert isinstance(get_provider_for("http://localhost:1234/v1"), LMStudioLifecycle)
    assert isinstance(get_provider_for("http://localhost:11434/v1"), OllamaLifecycle)
    assert isinstance(get_provider_for("http://localhost:9999/v1"), StaticEndpointLifecycle)


# ---------------------------------------------------------------------------
# LM Studio strategy — native /api/v1/models/* dialect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lmstudio_load_skips_when_already_resident(monkeypatch):
    from src.core.intelligence.providers import lmstudio as mod

    # Residency comes from the NATIVE endpoint's `loaded_instances`, never the
    # OpenAI-compat /v1/models listing — that one lists every *downloaded* model on
    # LM Studio, so trusting it made every residency check answer "already loaded"
    # and silently disabled the whole coordinator (refs #592).
    routes = {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(
            200,
            {"models": [{"key": "qwen/qwen3.5-9b", "loaded_instances": [{"id": "inst-1"}]}]},
        ),
    }
    calls = _patch_httpx(monkeypatch, mod, routes)
    prov = LMStudioLifecycle("http://localhost:1234/v1")
    ok = await prov.load("qwen/qwen3.5-9b:Q4_K_M")  # quant suffix must still match
    assert ok is True
    # Idempotent: it listed, saw it resident, and issued NO /models/load POST.
    assert not any(m == "POST" and "models/load" in u for m, u, _ in calls)


@pytest.mark.asyncio
async def test_lmstudio_load_posts_native_endpoint_when_absent(monkeypatch):
    from src.core.intelligence.providers import lmstudio as mod

    routes = {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(200, {"models": []}),
        ("POST", "http://localhost:1234/api/v1/models/load"): _FakeResponse(200, {}),
    }
    calls = _patch_httpx(monkeypatch, mod, routes)
    prov = LMStudioLifecycle("http://localhost:1234/v1")
    ok = await prov.load("qwen/qwen3.5-9b", context_length=8192)
    assert ok is True
    load_calls = [(u, body) for m, u, body in calls if m == "POST"]
    assert load_calls, "expected a native /api/v1/models/load POST"
    url, body = load_calls[0]
    assert url == "http://localhost:1234/api/v1/models/load"
    assert body["model"] == "qwen/qwen3.5-9b"
    assert body["context_length"] == 8192


@pytest.mark.asyncio
async def test_lmstudio_list_loaded_ignores_downloaded_but_unloaded(monkeypatch):
    """Residency must mean *resident*, not *downloaded* (refs #592).

    LM Studio's OpenAI-compat ``/v1/models`` lists every downloaded model. Believing it
    made ``list_loaded()`` report ~18 models on an idle server, which in turn made
    ``load()`` skip every load, ``_ensure_resident_impl`` return True immediately, and
    the wait-narrator's "only if already resident" guard always pass — so models were
    left to JIT-load, where LM Studio's Auto-Evict kills them mid-load.
    """
    from src.core.intelligence.providers import lmstudio as mod

    routes = {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(
            200,
            {"models": [
                {"key": "qwen/qwen3.5-9b", "loaded_instances": [{"id": "inst-1"}]},
                {"key": "google/gemma-4-26b-a4b-qat", "loaded_instances": []},
                {"key": "qwen/qwen3-coder-30b", "loaded_instances": []},
            ]},
        ),
    }
    _patch_httpx(monkeypatch, mod, routes)
    prov = LMStudioLifecycle("http://localhost:1234/v1")
    assert await prov.list_loaded() == ["qwen/qwen3.5-9b"]


@pytest.mark.asyncio
async def test_lmstudio_load_strips_provider_prefix(monkeypatch):
    """The load payload must carry the backend's own id, not LiteLLM's prefixed one.

    Callers pass the router/adapter name (``openai/google/gemma-4-26b-a4b-qat``);
    LM Studio answers ``model_not_found`` for that, so the explicit load silently failed
    and the model JIT-loaded instead (refs #592).
    """
    from src.core.intelligence.providers import lmstudio as mod

    routes = {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(200, {"models": []}),
        ("POST", "http://localhost:1234/api/v1/models/load"): _FakeResponse(200, {}),
    }
    calls = _patch_httpx(monkeypatch, mod, routes)
    prov = LMStudioLifecycle("http://localhost:1234/v1")
    assert await prov.load("openai/google/gemma-4-26b-a4b-qat") is True
    body = [b for m, _u, b in calls if m == "POST"][0]
    assert body["model"] == "google/gemma-4-26b-a4b-qat"


@pytest.mark.asyncio
async def test_lmstudio_load_refuses_when_residency_probe_fails(monkeypatch):
    """An unreachable listing must NOT be read as "nothing is resident".

    `list_loaded()` returns [] both for "nothing loaded" and "probe failed". Conflating
    them here would skip the duplicate-instance guard and issue a load for a model that
    is already resident — LM Studio then spins up a SECOND instance, and two heavy models
    at once OOMs the box.
    """
    from src.core.intelligence.providers import lmstudio as mod

    routes = {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(503, {}),
        ("POST", "http://localhost:1234/api/v1/models/load"): _FakeResponse(200, {}),
    }
    calls = _patch_httpx(monkeypatch, mod, routes)
    prov = LMStudioLifecycle("http://localhost:1234/v1")
    assert await prov.load("qwen/qwen3.5-9b") is False
    assert not any(m == "POST" for m, _u, _b in calls), "must not load on an unconfirmed probe"


@pytest.mark.asyncio
async def test_lmstudio_unload_resolves_instance_id(monkeypatch):
    from src.core.intelligence.providers import lmstudio as mod

    routes = {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(
            200,
            {"models": [{"key": "qwen/qwen3.5-9b", "loaded_instances": [{"id": "inst-42"}]}]},
        ),
        ("POST", "http://localhost:1234/api/v1/models/unload"): _FakeResponse(200, {}),
    }
    calls = _patch_httpx(monkeypatch, mod, routes)
    prov = LMStudioLifecycle("http://localhost:1234/v1")
    ok = await prov.unload("qwen/qwen3.5-9b")
    assert ok is True
    unload = [(u, body) for m, u, body in calls if m == "POST"][0]
    assert unload[0] == "http://localhost:1234/api/v1/models/unload"
    assert unload[1] == {"instance_id": "inst-42"}


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"data": []}, {"models": None}, {"models": {}},
    {"models": [None]}, {"models": [{"key": "main"}]},
    {"models": [{"key": "main", "loaded_instances": "unknown"}]},
    {"models": [{"key": "main", "loaded_instances": [{}]}]},
])
async def test_lmstudio_load_refuses_ambiguous_inventory(monkeypatch, body):
    from src.core.intelligence.providers import lmstudio as mod

    calls = _patch_httpx(monkeypatch, mod, {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(200, body),
        ("POST", "http://localhost:1234/api/v1/models/load"): _FakeResponse(200, {}),
    })
    assert await LMStudioLifecycle("http://localhost:1234/v1").load("main") is False
    assert not any(method == "POST" for method, _url, _body in calls)


@pytest.mark.asyncio
async def test_lmstudio_load_recognizes_custom_instance_name(monkeypatch):
    from src.core.intelligence.providers import lmstudio as mod

    calls = _patch_httpx(monkeypatch, mod, {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(200, {
            "models": [{"key": "example/main", "loaded_instances": [{"id": "chosen-name"}]}],
        }),
        ("POST", "http://localhost:1234/api/v1/models/load"): _FakeResponse(200, {}),
    })
    assert await LMStudioLifecycle("http://localhost:1234/v1").load("chosen-name") is True
    assert not any(method == "POST" for method, _url, _body in calls)


@pytest.mark.asyncio
async def test_lmstudio_unload_noop_when_not_loaded(monkeypatch):
    from src.core.intelligence.providers import lmstudio as mod

    routes = {
        ("GET", "http://localhost:1234/api/v1/models"): _FakeResponse(200, {"models": []}),
    }
    _patch_httpx(monkeypatch, mod, routes)
    prov = LMStudioLifecycle("http://localhost:1234/v1")
    assert await prov.unload("qwen/qwen3.5-9b") is False


# ---------------------------------------------------------------------------
# Ollama strategy — keep_alive + /api/ps
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ollama_list_loaded_uses_api_ps(monkeypatch):
    from src.core.intelligence.providers import ollama as mod

    routes = {
        ("GET", "http://localhost:11434/api/ps"): _FakeResponse(
            200, {"models": [{"name": "qwen2.5:latest"}]}
        ),
    }
    _patch_httpx(monkeypatch, mod, routes)
    prov = OllamaLifecycle("http://localhost:11434/v1")
    assert await prov.list_loaded() == ["qwen2.5:latest"]


@pytest.mark.asyncio
async def test_ollama_load_sets_keep_alive_negative_one(monkeypatch):
    from src.core.intelligence.providers import ollama as mod

    routes = {
        ("GET", "http://localhost:11434/api/ps"): _FakeResponse(200, {"models": []}),
        ("POST", "http://localhost:11434/api/generate"): _FakeResponse(200, {}),
    }
    calls = _patch_httpx(monkeypatch, mod, routes)
    prov = OllamaLifecycle("http://localhost:11434/v1")
    ok = await prov.load("ollama/qwen2.5")  # prefix must be stripped
    assert ok is True
    post = [(u, body) for m, u, body in calls if m == "POST"][0]
    assert post[0] == "http://localhost:11434/api/generate"
    assert post[1]["model"] == "qwen2.5"
    assert post[1]["keep_alive"] == -1


@pytest.mark.asyncio
async def test_ollama_unload_sets_keep_alive_zero(monkeypatch):
    from src.core.intelligence.providers import ollama as mod

    routes = {("POST", "http://localhost:11434/api/generate"): _FakeResponse(200, {})}
    calls = _patch_httpx(monkeypatch, mod, routes)
    prov = OllamaLifecycle("http://localhost:11434/v1")
    ok = await prov.unload("qwen2.5")
    assert ok is True
    assert calls[0][2]["keep_alive"] == 0


# ---------------------------------------------------------------------------
# Static endpoint (llama.cpp / vLLM) — no-op load/unload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_static_load_is_noop_true_unload_false():
    prov = StaticEndpointLifecycle("http://localhost:8000/v1")
    assert prov.supports_explicit_load is False
    assert prov.supports_unload is False
    assert await prov.load("whatever-model") is True
    assert await prov.unload("whatever-model") is False


@pytest.mark.asyncio
async def test_static_list_loaded_uses_openai_models(monkeypatch):
    from src.core.intelligence.providers import base as mod

    routes = {
        ("GET", "http://localhost:8000/v1/models"): _FakeResponse(
            200, {"data": [{"id": "meta-llama/Llama-3.1-8B"}]}
        ),
    }
    _patch_httpx(monkeypatch, mod, routes)
    prov = StaticEndpointLifecycle("http://localhost:8000/v1")
    assert await prov.list_loaded() == ["meta-llama/Llama-3.1-8B"]
