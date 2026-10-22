"""Tiered fetch + extract pipeline for ``web_search`` (no LLM)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

from src.tools.web.content_extraction import (
    extract_from_html,
    llm_friendliness_score,
    passes_quality_gate,
    select_page_or_snippet,
)
from src.tools.web.media_metadata import parse_media_metadata
from src.tools.web.playwright_fetch import (
    fetch_rendered_html,
    mark_playwright_preferred,
    playwright_fallback_enabled,
    prefer_playwright_for_url,
)
from src.tools.web.video_routing import detect_video_platform, resolve_video_oembed

# Content with score below this after Tier-1 may trigger Playwright retry.
_MIN_TIER1_SCORE = 25


def _parse_fetch_html_result(resp: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    if not resp.get("success"):
        return None, str(resp.get("error") or "fetch failed")
    raw = resp.get("result")
    if isinstance(raw, list) and raw:
        first = raw[0]
        html = first["text"] if isinstance(first, dict) and "text" in first else str(first)
    else:
        html = str(raw) if raw else ""
    if not html or len(html.strip()) < 20:
        return None, "fetched body empty/too short"
    return html, None


def _finalize(html: str, hit: Dict[str, str], max_chars: int, *, source_prefix: str = "") -> Dict[str, Any]:
    extracted = extract_from_html(html, max_chars=max_chars)
    content, source, ok, note = select_page_or_snippet(
        extracted, hit.get("description", ""), max_chars=max_chars
    )
    if source_prefix and ok and not source.startswith("search_snippet"):
        source = f"{source_prefix}:{source}"
    media = parse_media_metadata(html, hit.get("url"), dom_image_fallback=source_prefix == "playwright")
    result = {
        "content": content,
        "content_source": source if ok else None,
        "success": ok,
        "error": note,
        "score": llm_friendliness_score(content) if ok else 0.0,
    }
    if media["images"] or media["videos"]:
        result["media"] = media
    return result


def _snippet_only(hit: Dict[str, str], max_chars: int, reason: str) -> Dict[str, Any]:
    snippet = (hit.get("description") or "").strip()[:max_chars]
    ok, _ = passes_quality_gate(snippet)
    if ok:
        return {
            "content": snippet,
            "content_source": "search_snippet",
            "success": True,
            "error": reason,
            "score": llm_friendliness_score(snippet),
        }
    return {
        "content": "",
        "content_source": None,
        "success": False,
        "error": reason,
        "score": 0.0,
    }


async def resolve_url_content(
    registry: Any,
    hit: Dict[str, str],
    *,
    fetch_timeout: float,
    max_chars: int,
) -> Dict[str, Any]:
    """Fetch and extract readable content for one search hit."""
    url = hit["url"]
    best: Dict[str, Any] = {"content": "", "content_source": None, "success": False, "error": None, "score": 0.0}

    # Video watch pages: skip the HTML tiers entirely (SPA/bot-walled), pull
    # authoritative title/thumbnail via the platform's public oEmbed lookup.
    if detect_video_platform(url):
        oembed = await resolve_video_oembed(url, timeout=fetch_timeout)
        if oembed:
            title = oembed["title"]
            media: Dict[str, Any] = {
                "images": (
                    [{"url": oembed["thumbnail_url"], "alt": title, "role": "oembed:thumbnail"}]
                    if oembed.get("thumbnail_url")
                    else []
                ),
                "videos": [{"url": url, "title": title, "platform": oembed["platform"]}],
            }
            return {
                "content": title,
                "content_source": f"oembed:{oembed['platform']}",
                "success": True,
                "error": None,
                "score": llm_friendliness_score(title),
                "media": media,
            }
        # oEmbed failed (private/deleted video) — fall through to normal tiers.

    async def _try_html(html: str, prefix: str) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: _finalize(html, hit, max_chars, source_prefix=prefix)
        )

    # Tier 2 first for known-JS domains (skip wasted Tier-1 round-trip).
    if prefer_playwright_for_url(url) and playwright_fallback_enabled():
        pw_html, pw_err = await fetch_rendered_html(url)
        if pw_html:
            result = await _try_html(pw_html, "playwright")
            if result["success"] and result["score"] >= _MIN_TIER1_SCORE:
                mark_playwright_preferred(url)
                return result
            if result["score"] > best["score"]:
                best = result

    # Tier 1: fetch_html MCP
    try:
        resp = await asyncio.wait_for(
            registry.execute_tool("fetch_html", {"url": url}),
            timeout=fetch_timeout,
        )
    except asyncio.TimeoutError:
        resp = {"success": False, "error": "fetch timed out"}
    except Exception as exc:
        resp = {"success": False, "error": f"fetch_html failed: {exc}"}

    html, fetch_err = _parse_fetch_html_result(resp if isinstance(resp, dict) else {})
    if html:
        tier1 = await _try_html(html, "fetch_html")
        if tier1["success"] and tier1["score"] >= _MIN_TIER1_SCORE:
            return tier1
        if tier1["score"] > best["score"]:
            best = tier1
    elif fetch_err and best["score"] == 0:
        best["error"] = fetch_err

    # Tier 2: Playwright render (JS SPAs, soft blocks)
    needs_playwright = (
        playwright_fallback_enabled()
        and (not html or not best["success"] or best["score"] < _MIN_TIER1_SCORE)
    )
    if needs_playwright and not prefer_playwright_for_url(url):
        pw_html, pw_err = await fetch_rendered_html(url)
        if pw_html:
            tier2 = await _try_html(pw_html, "playwright")
            if tier2["success"] and tier2["score"] >= _MIN_TIER1_SCORE:
                mark_playwright_preferred(url)
                return tier2
            if tier2["score"] > best["score"]:
                best = tier2
        elif pw_err and not best["error"]:
            best["error"] = pw_err

    if best["success"]:
        return best

    snippet_result = _snippet_only(hit, max_chars, best["error"] or "no usable page content")
    if snippet_result["success"]:
        return snippet_result
    return best if best["error"] else snippet_result
