import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.remote import endpoints
from src.shared.config_manager import ConfigManager


@pytest.mark.asyncio
async def test_ping_http_true_on_2xx(monkeypatch):
    class FakeResponse:
        status_code = 200

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(endpoints.httpx, "AsyncClient", lambda **kw: FakeClient())
    assert await endpoints._ping_http("http://example.invalid") is True


@pytest.mark.asyncio
async def test_ping_http_false_on_exception(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise ConnectionError("refused")

    monkeypatch.setattr(endpoints.httpx, "AsyncClient", lambda **kw: FakeClient())
    assert await endpoints._ping_http("http://example.invalid") is False


def test_check_typst_reports_binary_state(monkeypatch):
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: "typst"))
    result = endpoints._check_typst()
    assert result["id"] == "typst"
    assert result["tier"] == "enhanced"
    assert isinstance(result["healthy"], bool)


def test_check_marp_defaults_to_npx(monkeypatch):
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: None))
    result = endpoints._check_marp()
    assert result["id"] == "marp"
    assert result["tier"] == "enhanced"
    assert isinstance(result["healthy"], bool)


@pytest.mark.asyncio
async def test_services_health_endpoint_shape(monkeypatch):
    from src.infrastructure.services import presenton_sidecar

    async def always_healthy(url, timeout=3.0):
        return True

    monkeypatch.setattr(endpoints, "_ping_http", always_healthy)
    monkeypatch.setattr(presenton_sidecar, "_presenton_connected", always_healthy)
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: default))

    result = await endpoints._services_health_endpoint(request=None)
    payload = json.loads(result.body)

    ids = {s["id"] for s in payload["services"]}
    assert ids == {
        "brain", "lm_studio", "searxng", "kroki", "typst", "marp", "presenton",
        "cron_tasks", "reflection_lessons", "kanban", "workflows", "dashboard_feed",
        "oats", "telegram", "multi_user_auth", "openverse",
    }

    cron = next(s for s in payload["services"] if s["id"] == "cron_tasks")
    assert cron["mode"] in ("local", "cloud")
    multi_user_auth = next(s for s in payload["services"] if s["id"] == "multi_user_auth")
    assert multi_user_auth["mode"] == "cloud"

    brain = next(s for s in payload["services"] if s["id"] == "brain")
    assert brain == {"id": "brain", "label": "Brain", "tier": "core", "healthy": True, "detail": None}

    lm_studio = next(s for s in payload["services"] if s["id"] == "lm_studio")
    assert lm_studio["healthy"] is True
    assert lm_studio["tier"] == "core"
