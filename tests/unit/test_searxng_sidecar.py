"""Safeguards for one-click SearXNG connection and provisioning."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.services import searxng_sidecar as ss


@pytest.mark.asyncio
async def test_connected_searxng_skips_docker():
    compose = AsyncMock()
    with patch.object(
        ss.ConfigManager,
        "get",
        return_value="http://127.0.0.1:8080",
    ), patch.object(
        ss, "_search_api_connected", new_callable=AsyncMock, return_value=True
    ), patch.object(ss.asyncio, "to_thread", compose):
        result = await ss.start_searxng_sidecar()

    assert result["success"] is True
    assert result["connected"] is True
    assert result["provisioned"] is False
    compose.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreachable_external_searxng_skips_local_docker():
    compose = AsyncMock()
    with patch.object(
        ss.ConfigManager,
        "get",
        return_value="https://search.example.test",
    ), patch.object(
        ss, "_search_api_connected", new_callable=AsyncMock, return_value=False
    ), patch.object(ss.asyncio, "to_thread", compose):
        result = await ss.start_searxng_sidecar()

    assert result["success"] is False
    assert result["connected"] is False
    assert result["provisioned"] is False
    assert "external SearXNG" in result["message"]
    compose.assert_not_awaited()


# --- set_searxng_settings (issue #523: Serper/Tavily/all backend picker) ------- #

def _config_get(key, default=None):
    return {"tools.searxng_url": "http://127.0.0.1:8080", "tools.search_backend": "searxng"}.get(key, default)


def test_set_searxng_settings_none_is_a_read_only_noop():
    with patch.object(ss.ConfigManager, "get", side_effect=_config_get):
        result = ss.set_searxng_settings(search_backend=None)
    assert result == {"success": True, "url": "http://127.0.0.1:8080", "search_backend": "searxng"}


def test_set_searxng_settings_rejects_invalid_value():
    with patch.object(ss.ConfigManager, "get", side_effect=_config_get):
        result = ss.set_searxng_settings(search_backend="not-a-real-backend")
    assert result["success"] is False
    assert "Invalid search_backend" in result["error"]


def test_set_searxng_settings_persists_and_hot_reloads():
    fake_repo = MagicMock()
    fake_repo.load.return_value = {"tools": {}}

    with patch("src.infrastructure.repositories.ConfigRepository", return_value=fake_repo), \
         patch.object(ss.ConfigManager, "get", return_value="all"), \
         patch.object(ss.ConfigManager, "_recursive_update") as recursive_update, \
         patch.object(ss.ConfigManager, "get_config", return_value={"tools": {}}):
        result = ss.set_searxng_settings(search_backend="all")

    assert result["success"] is True
    fake_repo.save.assert_called_once()
    saved_name, saved_prefs = fake_repo.save.call_args[0][:2]
    assert saved_name == "preferences"
    assert saved_prefs["tools"]["search_backend"] == "all"
    recursive_update.assert_called_once_with({"tools": {}}, {"tools": {"search_backend": "all"}})


@pytest.mark.parametrize("backend", sorted(ss.VALID_SEARCH_BACKENDS))
def test_set_searxng_settings_accepts_every_valid_backend(backend):
    fake_repo = MagicMock()
    fake_repo.load.return_value = {"tools": {}}

    with patch("src.infrastructure.repositories.ConfigRepository", return_value=fake_repo), \
         patch.object(ss.ConfigManager, "get", return_value=backend), \
         patch.object(ss.ConfigManager, "_recursive_update"), \
         patch.object(ss.ConfigManager, "get_config", return_value={"tools": {}}):
        result = ss.set_searxng_settings(search_backend=backend)

    assert result["success"] is True
    assert result["search_backend"] == backend


# --- get_search_quota (issue #523: quota display) ------------------------------ #

@pytest.mark.asyncio
async def test_get_search_quota_reports_unconfigured_backends():
    with patch.object(ss.ConfigManager, "get_secret", return_value=None):
        result = await ss.get_search_quota()
    assert result == {
        "tavily": {"configured": False, "available": False},
        "serper": {"configured": False, "available": False},
        "brave": {"configured": False, "available": False},
    }


@pytest.mark.asyncio
async def test_get_search_quota_uses_live_tavily_usage_when_configured():
    fake_usage = {"success": True, "used": 3, "limit": 1000, "remaining": 997, "plan": "Researcher"}
    with patch.object(ss.ConfigManager, "get_secret", return_value="fake-key"), \
         patch("src.tools.web.tavily_search.get_tavily_usage", new=AsyncMock(return_value=fake_usage)):
        result = await ss.get_search_quota()

    assert result["tavily"] == {"configured": True, "available": True, **fake_usage}
    # Serper/Brave have no live-usage API — reported honestly, not faked.
    assert result["serper"]["available"] is False
    assert result["brave"]["available"] is False
    assert "note" in result["serper"]
    assert "note" in result["brave"]
