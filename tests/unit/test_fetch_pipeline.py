"""Unit tests for web_search fetch tier escalation (mocked, no network)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.web.fetch_pipeline import resolve_url_content

GOOD_HTML = """
<html><body><main>
<h1>Wedding Videography Packages</h1>
<p>Our eight-hour wedding package includes two videographers, drone coverage,
and a highlight film. Packages start at $3600 plus tax for North Alabama couples.
Travel fees may apply for destinations outside the Huntsville metro area.</p>
<p>We specialize in documentary-style storytelling and cinematic wedding films.</p>
</main></body></html>
"""

GOOD_HTML_WITH_OG_IMAGE = """
<html><head>
<meta property="og:image" content="https://example.com/hero.jpg">
</head><body><main>
<h1>Wedding Videography Packages</h1>
<p>Our eight-hour wedding package includes two videographers, drone coverage,
and a highlight film. Packages start at $3600 plus tax for North Alabama couples.
Travel fees may apply for destinations outside the Huntsville metro area.</p>
<p>We specialize in documentary-style storytelling and cinematic wedding films.</p>
</main></body></html>
"""

THIN_HTML = "<html><body><nav>Weddings branding Real Estate CORDOVA MEDIA</nav></body></html>"


class _Registry:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def execute_tool(self, name: str, args: dict) -> dict:
        self.calls += 1
        assert name == "fetch_html"
        if not self._responses:
            return {"success": False, "error": "no mock left"}
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_tier1_fetch_html_wins_without_playwright():
    reg = _Registry([{"success": True, "result": [{"text": GOOD_HTML}]}])
    with patch("src.tools.web.fetch_pipeline.fetch_rendered_html", new_callable=AsyncMock) as pw:
        out = await resolve_url_content(
            reg,
            {"url": "https://example.com/packages", "description": "short snippet"},
            fetch_timeout=5.0,
            max_chars=6000,
        )
    assert out["success"]
    assert out["content_source"] == "fetch_html:trafilatura_precision"
    assert out["score"] >= 25
    pw.assert_not_called()


@pytest.mark.asyncio
async def test_tier2_playwright_when_tier1_thin():
    reg = _Registry([{"success": True, "result": [{"text": THIN_HTML}]}])
    with patch(
        "src.tools.web.fetch_pipeline.fetch_rendered_html",
        new_callable=AsyncMock,
        return_value=(GOOD_HTML, None),
    ) as pw:
        out = await resolve_url_content(
            reg,
            {"url": "https://mcordovamedia.com/", "description": "thin snippet fallback"},
            fetch_timeout=5.0,
            max_chars=6000,
        )
    assert out["success"]
    assert out["content_source"] == "playwright:trafilatura_precision"
    assert out["score"] >= 25
    pw.assert_called_once()


@pytest.mark.asyncio
async def test_tier3_snippet_when_fetch_and_playwright_fail():
    snippet = (
        "North Alabama Wedding and Production Videographer traveling to capture "
        "your special moments with cinematic documentary style."
    )
    reg = _Registry([{"success": False, "error": "HTTP 403"}])
    with patch(
        "src.tools.web.fetch_pipeline.fetch_rendered_html",
        new_callable=AsyncMock,
        return_value=(None, "playwright HTTP 403"),
    ):
        out = await resolve_url_content(
            reg,
            {"url": "https://www.weddingwire.com/biz/example/", "description": snippet},
            fetch_timeout=5.0,
            max_chars=6000,
        )
    assert out["success"]
    assert out["content_source"] == "search_snippet"
    assert "North Alabama" in out["content"]


@pytest.mark.asyncio
async def test_media_metadata_attached_when_og_image_present():
    reg = _Registry([{"success": True, "result": [{"text": GOOD_HTML_WITH_OG_IMAGE}]}])
    with patch("src.tools.web.fetch_pipeline.fetch_rendered_html", new_callable=AsyncMock) as pw:
        out = await resolve_url_content(
            reg,
            {"url": "https://example.com/packages", "description": "short snippet"},
            fetch_timeout=5.0,
            max_chars=6000,
        )
    assert out["success"]
    assert out["media"]["images"] == [
        {"url": "https://example.com/hero.jpg", "alt": "", "role": "og:image"}
    ]
    pw.assert_not_called()


@pytest.mark.asyncio
async def test_no_media_key_when_no_og_tags_present():
    reg = _Registry([{"success": True, "result": [{"text": GOOD_HTML}]}])
    out = await resolve_url_content(
        reg,
        {"url": "https://example.com/packages", "description": "short snippet"},
        fetch_timeout=5.0,
        max_chars=6000,
    )
    assert out["success"]
    assert "media" not in out


@pytest.mark.asyncio
async def test_video_url_routes_to_oembed_and_skips_html_tiers():
    reg = _Registry([])  # would error if fetch_html were called
    oembed = {
        "url": "https://www.youtube.com/watch?v=abc12345678",
        "title": "Wedding Highlight Reel",
        "platform": "youtube",
        "thumbnail_url": "https://i.ytimg.com/thumb.jpg",
        "author_name": "Acme Films",
    }
    with patch(
        "src.tools.web.fetch_pipeline.resolve_video_oembed", new_callable=AsyncMock, return_value=oembed
    ), patch("src.tools.web.fetch_pipeline.fetch_rendered_html", new_callable=AsyncMock) as pw:
        out = await resolve_url_content(
            reg,
            {"url": "https://www.youtube.com/watch?v=abc12345678", "description": "snippet"},
            fetch_timeout=5.0,
            max_chars=6000,
        )
    assert out["success"]
    assert out["content"] == "Wedding Highlight Reel"
    assert out["content_source"] == "oembed:youtube"
    assert out["media"]["videos"] == [
        {"url": "https://www.youtube.com/watch?v=abc12345678", "title": "Wedding Highlight Reel", "platform": "youtube"}
    ]
    assert out["media"]["images"] == [
        {"url": "https://i.ytimg.com/thumb.jpg", "alt": "Wedding Highlight Reel", "role": "oembed:thumbnail"}
    ]
    assert reg.calls == 0
    pw.assert_not_called()


@pytest.mark.asyncio
async def test_video_url_falls_back_to_html_tiers_when_oembed_fails():
    reg = _Registry([{"success": True, "result": [{"text": GOOD_HTML}]}])
    with patch(
        "src.tools.web.fetch_pipeline.resolve_video_oembed", new_callable=AsyncMock, return_value=None
    ):
        out = await resolve_url_content(
            reg,
            {"url": "https://www.youtube.com/watch?v=deadbeef123", "description": "short snippet"},
            fetch_timeout=5.0,
            max_chars=6000,
        )
    assert out["success"]
    assert out["content_source"] == "fetch_html:trafilatura_precision"


@pytest.mark.asyncio
async def test_dom_hero_image_fallback_used_for_playwright_tier():
    html_with_hero_img = """
    <html><body><main>
    <img src="https://example.com/hero.jpg" width="1200" height="800" alt="Hero">
    <h1>Wedding Videography Packages</h1>
    <p>Our eight-hour wedding package includes two videographers, drone coverage,
    and a highlight film. Packages start at $3600 plus tax for North Alabama couples.
    Travel fees may apply for destinations outside the Huntsville metro area.</p>
    <p>We specialize in documentary-style storytelling and cinematic wedding films.</p>
    </main></body></html>
    """
    reg = _Registry([{"success": False, "error": "HTTP 403"}])
    with patch(
        "src.tools.web.fetch_pipeline.fetch_rendered_html",
        new_callable=AsyncMock,
        return_value=(html_with_hero_img, None),
    ):
        out = await resolve_url_content(
            reg,
            {"url": "https://example.com/packages", "description": "short snippet"},
            fetch_timeout=5.0,
            max_chars=6000,
        )
    assert out["success"]
    assert out["content_source"] == "playwright:trafilatura_precision"
    assert out["media"]["images"] == [
        {"url": "https://example.com/hero.jpg", "alt": "Hero", "role": "dom:hero"}
    ]


class _ErrorRegistry:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def execute_tool(self, name: str, args: dict) -> dict:
        self.calls += 1
        raise self._exc


@pytest.mark.asyncio
async def test_tier1_missing_tool_falls_back_gracefully():
    snippet = "Fallback snippet when tool is missing from registry."
    reg = _ErrorRegistry(ValueError("Tool 'fetch_html' not found in registry."))
    with patch(
        "src.tools.web.fetch_pipeline.fetch_rendered_html",
        new_callable=AsyncMock,
        return_value=(GOOD_HTML, None),
    ) as pw:
        out = await resolve_url_content(
            reg,
            {"url": "https://example.com/packages", "description": snippet},
            fetch_timeout=5.0,
            max_chars=6000,
        )
    assert out["success"]
    assert out["content_source"] == "playwright:trafilatura_precision"
    pw.assert_called_once()

