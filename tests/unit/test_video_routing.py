"""Unit tests for oEmbed-based video routing (issue #331, Phase 3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.web.video_routing import detect_video_platform, resolve_video_oembed


def test_detect_video_platform_youtube_watch_url():
    assert detect_video_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"


def test_detect_video_platform_youtu_be_url():
    assert detect_video_platform("https://youtu.be/dQw4w9WgXcQ") == "youtube"


def test_detect_video_platform_vimeo_url():
    assert detect_video_platform("https://vimeo.com/76979871") == "vimeo"


def test_detect_video_platform_none_for_regular_page():
    assert detect_video_platform("https://example.com/vendors/acme") is None


def _mock_client(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    client = AsyncMock()
    client.get.return_value = resp
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    return client


@pytest.mark.asyncio
async def test_resolve_video_oembed_youtube_success():
    data = {"title": "Highlight Reel", "thumbnail_url": "https://i.ytimg.com/thumb.jpg", "author_name": "Acme"}
    with patch("src.tools.web.video_routing.httpx.AsyncClient", return_value=_mock_client(data)):
        result = await resolve_video_oembed("https://www.youtube.com/watch?v=abc12345678")
    assert result == {
        "url": "https://www.youtube.com/watch?v=abc12345678",
        "title": "Highlight Reel",
        "platform": "youtube",
        "thumbnail_url": "https://i.ytimg.com/thumb.jpg",
        "author_name": "Acme",
    }


@pytest.mark.asyncio
async def test_resolve_video_oembed_returns_none_for_non_video_url():
    result = await resolve_video_oembed("https://example.com/vendors/acme")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_video_oembed_returns_none_on_http_error():
    with patch("src.tools.web.video_routing.httpx.AsyncClient", return_value=_mock_client({}, status_code=404)):
        result = await resolve_video_oembed("https://vimeo.com/999999999")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_video_oembed_returns_none_on_exception():
    client = AsyncMock()
    client.__aenter__.side_effect = RuntimeError("network down")
    with patch("src.tools.web.video_routing.httpx.AsyncClient", return_value=client):
        result = await resolve_video_oembed("https://vimeo.com/12345")
    assert result is None
