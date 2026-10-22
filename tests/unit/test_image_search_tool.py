"""Unit tests for ``search_images`` (Openverse / stock / SearXNG image search)."""
from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.web.image_search import ImageSearchTool, _clamp_count, _normalize_orientation


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self._payload


@pytest.fixture
def tool() -> ImageSearchTool:
    return ImageSearchTool()


def test_clamp_count_bounds():
    assert _clamp_count(0) == 1
    assert _clamp_count(3) == 3
    assert _clamp_count(99) == 12
    assert _clamp_count("nope") == 6


def test_active_search_image_backends_follows_config(monkeypatch):
    from src.shared.config_manager import ConfigManager
    from src.tools.web import image_search as mod

    monkeypatch.setattr(
        ConfigManager, "get",
        staticmethod(lambda key, default=None: "brave" if key == "tools.search_backend" else default),
    )
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: "bk" if key == "BRAVE_API_KEY" else default))
    assert mod._active_search_image_backends() == ["brave"]

    monkeypatch.setattr(
        ConfigManager, "get",
        staticmethod(lambda key, default=None: "serper" if key == "tools.search_backend" else default),
    )
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: "sk" if key == "SERPER_API_KEY" else default))
    assert mod._active_search_image_backends() == ["serper"]

    monkeypatch.setattr(
        ConfigManager, "get",
        staticmethod(lambda key, default=None: "searxng" if key == "tools.search_backend" else default),
    )
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda *a, **k: None))
    assert mod._active_search_image_backends() == ["searxng"]


@pytest.mark.asyncio
async def test_search_images_openverse_success(tool, monkeypatch):
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        ConfigManager, "get", staticmethod(lambda key, default=None: default)
    )

    openverse_payload = {
        "results": [
            {
                "url": "https://cdn.example/img.jpg",
                "title": "Neon alley",
                "creator": "Artist",
                "license": "by",
                "license_version": "4.0",
                "foreign_landing_url": "https://example.com/page",
                "thumbnail": "https://cdn.example/thumb.jpg",
                "width": 800,
                "height": 600,
            }
        ]
    }

    async def fake_get(url, params=None, headers=None):
        if "openverse.org" in str(url):
            return _FakeResponse(openverse_payload)
        raise AssertionError(f"unexpected url {url}")

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.tools.web.image_search.httpx.AsyncClient", return_value=fake_client):
        result = await tool.execute(query="anime aesthetic", count=3, provider="openverse")

    assert result["success"] is True
    assert len(result["images"]) == 1
    assert result["images"][0]["url"] == "https://cdn.example/img.jpg"
    assert result["images"][0]["provider"] == "openverse"
    assert "CC BY" in result["images"][0]["license"]
    assert "Neon alley" in result["result"]


@pytest.mark.asyncio
async def test_search_images_pexels_when_keyed(tool, monkeypatch):
    from src.shared.config_manager import ConfigManager

    def fake_secret(key: str, default: Optional[Any] = None):
        return "pexels-test-key" if key == "PEXELS_API_KEY" else default

    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(fake_secret))
    monkeypatch.setattr(
        ConfigManager, "get", staticmethod(lambda key, default=None: default)
    )

    pexels_payload = {
        "photos": [
            {
                "src": {
                    "large": "https://images.pexels.com/large.jpg",
                    "tiny": "https://images.pexels.com/tiny.jpg",
                },
                "photographer": "Pat",
                "alt": "Sushi",
                "url": "https://www.pexels.com/photo/1",
                "width": 1000,
                "height": 800,
            }
        ]
    }

    async def fake_get(url, params=None, headers=None):
        if "api.pexels.com" in str(url):
            assert headers and headers.get("Authorization") == "pexels-test-key"
            return _FakeResponse(pexels_payload)
        raise AssertionError(f"unexpected url {url}")

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.tools.web.image_search.httpx.AsyncClient", return_value=fake_client):
        result = await tool.execute(query="sushi", provider="pexels")

    assert result["success"] is True
    assert result["images"][0]["provider"] == "pexels"
    assert result["images"][0]["author"] == "Pat"


@pytest.mark.asyncio
async def test_search_images_searxng_fallback(tool, monkeypatch):
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(lambda key, default=None: {
            "tools.searxng_url": "http://127.0.0.1:8080",
            "tools.search_backend": "searxng",
        }.get(key, default)),
    )

    searx_payload = {
        "results": [
            {
                "title": "Fan art",
                "url": "https://page.example/art",
                "img_src": "https://img.example/art.png",
                "thumbnail_src": "https://img.example/thumb.png",
            }
        ]
    }

    async def fake_get(url, params=None, headers=None):
        assert "categories" in (params or {}) and params["categories"] == "images"
        return _FakeResponse(searx_payload)

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.tools.web.image_search.httpx.AsyncClient", return_value=fake_client):
        result = await tool.execute(query="neon tokyo", provider="searxng")

    assert result["success"] is True
    assert result["images"][0]["url"] == "https://img.example/art.png"
    assert result["images"][0]["provider"] == "searxng"


@pytest.mark.asyncio
async def test_provider_all_uses_active_brave_not_hardcoded_searx(tool, monkeypatch):
    """When Openverse is empty and search_backend=brave, fall back to Brave images."""
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(
        ConfigManager, "get_secret",
        staticmethod(lambda key, default=None: "brave-key" if key == "BRAVE_API_KEY" else default),
    )
    monkeypatch.setattr(
        ConfigManager, "get",
        staticmethod(lambda key, default=None: "brave" if key == "tools.search_backend" else default),
    )

    async def fake_get(url, params=None, headers=None):
        url_s = str(url)
        if "openverse.org" in url_s:
            return _FakeResponse({"results": []})
        if "api.search.brave.com" in url_s:
            assert headers.get("X-Subscription-Token") == "brave-key"
            return _FakeResponse({
                "results": [{
                    "title": "Neon",
                    "properties": {"url": "https://cdn.brave.example/n.jpg"},
                    "thumbnail": {"src": "https://cdn.brave.example/t.jpg"},
                    "meta_url": {"url": "https://page.example"},
                }]
            })
        raise AssertionError(f"unexpected url {url_s}")

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.tools.web.image_search.httpx.AsyncClient", return_value=fake_client):
        result = await tool.execute(query="neon", provider="all", count=2)

    assert result["success"] is True
    assert result["images"][0]["provider"] == "brave"
    assert "searxng" not in result["data"]["providers_used"]


@pytest.mark.asyncio
async def test_search_images_empty_query(tool):
    result = await tool.execute(query="  ")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_search_images_all_providers_fail(tool, monkeypatch):
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        ConfigManager, "get", staticmethod(lambda key, default=None: default)
    )

    async def fake_get(url, params=None, headers=None):
        raise RuntimeError("network down")

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.tools.web.image_search.httpx.AsyncClient", return_value=fake_client):
        result = await tool.execute(query="cats", provider="openverse")

    assert result["success"] is False
    assert "No images found" in result["error"]
