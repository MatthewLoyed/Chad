"""
Dedicated video routing for ``web_search`` (issue #331, Phase 3).

When a search hit's URL is itself a YouTube/Vimeo watch page, skip the
normal HTML fetch+extract tiers (video SPAs are bot-walled or yield junk
prose) and pull authoritative title/thumbnail via the platform's public
oEmbed endpoint instead — cheap, no API key, no hallucinated titles.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

from src.shared.logger import setup_logger
from src.tools.media.show_youtube_video import extract_youtube_video_id

logger = setup_logger("VideoRouting")

_YOUTUBE_OEMBED = "https://www.youtube.com/oembed?url={url}&format=json"
_VIMEO_OEMBED = "https://vimeo.com/api/oembed.json?url={url}"


def detect_video_platform(url: str) -> Optional[str]:
    """Return ``"youtube"``/``"vimeo"`` if ``url`` is a video watch page, else ``None``."""
    if not url:
        return None
    if extract_youtube_video_id(url):
        return "youtube"
    lowered = url.lower()
    if "vimeo.com" in lowered:
        return "vimeo"
    return None


async def resolve_video_oembed(url: str, *, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Fetch oEmbed metadata for a known video URL. Returns ``None`` on any failure."""
    platform = detect_video_platform(url)
    if not platform:
        return None

    oembed_url = (
        _YOUTUBE_OEMBED.format(url=quote(url, safe=""))
        if platform == "youtube"
        else _VIMEO_OEMBED.format(url=quote(url, safe=""))
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(oembed_url)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:
        logger.warning(f"[ VIDEO ROUTING ] oEmbed lookup failed for {url}: {exc}")
        return None

    title = str(data.get("title") or "").strip()
    if not title:
        return None
    return {
        "url": url,
        "title": title,
        "platform": platform,
        "thumbnail_url": data.get("thumbnail_url"),
        "author_name": data.get("author_name"),
    }
