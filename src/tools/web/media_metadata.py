"""
Cheap rich-media extraction for ``web_search`` (issue #331, Phases 1-2).

Parses ``<meta property="og:*">`` / Twitter card tags and JSON-LD
``VideoObject`` blocks out of HTML that ``fetch_pipeline`` already fetched —
no additional network request. When rendered (Playwright) HTML has no OG
image, falls back to a heuristic DOM hero-image scan (largest qualifying
``<img>``, resolving ``srcset``/lazy-load attrs the JS render already
populated). See ``docs/features/WEB_SEARCH_MEDIA.md``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from lxml import html as lxml_html

_MAX_IMAGES = 5
_MAX_VIDEOS = 3

_OG_IMAGE_PROPS = ("og:image", "og:image:url", "og:image:secure_url")
_TWITTER_IMAGE_NAMES = ("twitter:image", "twitter:image:src")

_LAZY_SRC_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-lazy", "data-image")
_SKIP_SRC_PATTERNS = ("logo", "icon", "sprite", "avatar", "pixel.", "spacer", "1x1", "blank.gif", "transparent.png")
_CONTENT_ANCESTOR_TAGS = ("main", "article", "figure")
_MIN_DOM_IMAGE_DIM = 100


def _resolve(base_url: Optional[str], maybe_url: Optional[str]) -> Optional[str]:
    if not maybe_url:
        return None
    maybe_url = maybe_url.strip()
    if not maybe_url:
        return None
    if base_url:
        return urljoin(base_url, maybe_url)
    return maybe_url


def _extract_og_images(tree: Any, base_url: Optional[str]) -> List[Dict[str, str]]:
    images: List[Dict[str, str]] = []
    seen: set[str] = set()

    def _add(url: Optional[str], role: str, alt: str = "") -> None:
        resolved = _resolve(base_url, url)
        if not resolved or resolved in seen:
            return
        seen.add(resolved)
        images.append({"url": resolved, "alt": alt, "role": role})

    for meta in tree.xpath("//meta[@property]"):
        prop = (meta.get("property") or "").lower()
        if prop in _OG_IMAGE_PROPS:
            _add(meta.get("content"), "og:image")
        elif prop == "og:image:alt" and images:
            images[-1]["alt"] = (meta.get("content") or "").strip()

    for meta in tree.xpath("//meta[@name]"):
        name = (meta.get("name") or "").lower()
        if name in _TWITTER_IMAGE_NAMES:
            _add(meta.get("content"), "twitter:image")

    return images[:_MAX_IMAGES]


def _platform_for_url(url: str) -> Optional[str]:
    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    if "vimeo.com" in lowered:
        return "vimeo"
    return None


def _extract_json_ld_videos(tree: Any, base_url: Optional[str]) -> List[Dict[str, str]]:
    videos: List[Dict[str, str]] = []
    seen: set[str] = set()

    for script in tree.xpath('//script[@type="application/ld+json"]'):
        raw = script.text_content()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph")
            nodes = graph if isinstance(graph, list) else [candidate]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = node.get("@type")
                types = node_type if isinstance(node_type, list) else [node_type]
                if "VideoObject" not in types:
                    continue
                watch_url = node.get("embedUrl") or node.get("contentUrl") or node.get("url")
                resolved = _resolve(base_url, watch_url if isinstance(watch_url, str) else None)
                if not resolved or resolved in seen:
                    continue
                seen.add(resolved)
                videos.append(
                    {
                        "url": resolved,
                        "title": (node.get("name") or "").strip(),
                        "platform": _platform_for_url(resolved) or "unknown",
                    }
                )
                if len(videos) >= _MAX_VIDEOS:
                    return videos
    return videos


def _pick_srcset_url(srcset: str) -> Optional[str]:
    """Return the last (largest, by convention) candidate URL in a ``srcset`` value."""
    candidates: List[str] = []
    for part in srcset.split(","):
        bits = part.strip().split()
        if bits:
            candidates.append(bits[0])
    return candidates[-1] if candidates else None


def _img_src(img: Any) -> Optional[str]:
    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        picked = _pick_srcset_url(srcset)
        if picked:
            return picked
    for attr in ("src", *_LAZY_SRC_ATTRS):
        val = img.get(attr)
        if val:
            return val
    return None


def _looks_like_content_image(url: str) -> bool:
    lowered = url.lower()
    if lowered.startswith("data:"):
        return False
    return not any(pattern in lowered for pattern in _SKIP_SRC_PATTERNS)


def _extract_dom_hero_image(tree: Any, base_url: Optional[str]) -> List[Dict[str, str]]:
    """Heuristic fallback: pick the largest plausible content ``<img>`` in the DOM.

    Only meaningful against rendered (Playwright) HTML, where JS has already
    resolved lazy-load ``data-src``/``srcset`` attributes into the live DOM.
    """
    best: Optional[Dict[str, str]] = None
    best_score = -1
    for img in tree.xpath("//img"):
        raw_src = _img_src(img)
        if not raw_src:
            continue
        resolved = _resolve(base_url, raw_src)
        if not resolved or not _looks_like_content_image(resolved):
            continue

        try:
            width = int(img.get("width") or 0)
        except ValueError:
            width = 0
        try:
            height = int(img.get("height") or 0)
        except ValueError:
            height = 0
        if (width and width < _MIN_DOM_IMAGE_DIM) or (height and height < _MIN_DOM_IMAGE_DIM):
            continue

        in_content = any(img.xpath(f"ancestor::{tag}") for tag in _CONTENT_ANCESTOR_TAGS)
        score = (width * height if width and height else 0) + (100_000 if in_content else 0)
        if score > best_score:
            best_score = score
            best = {"url": resolved, "alt": (img.get("alt") or "").strip(), "role": "dom:hero"}

    return [best] if best else []


def parse_media_metadata(
    html: str,
    base_url: Optional[str] = None,
    *,
    dom_image_fallback: bool = False,
) -> Dict[str, List[Dict[str, str]]]:
    """Parse OG/Twitter image tags + JSON-LD ``VideoObject`` blocks from raw HTML.

    Returns ``{"images": [...], "videos": [...]}``; either list may be empty.
    Never raises — malformed HTML/JSON yields an empty result. When
    ``dom_image_fallback`` is set and no OG/Twitter image was found, scans the
    DOM for a plausible hero ``<img>`` (see ``_extract_dom_hero_image``).
    """
    result: Dict[str, List[Dict[str, str]]] = {"images": [], "videos": []}
    if not html or not html.strip():
        return result
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return result

    try:
        result["images"] = _extract_og_images(tree, base_url)
    except Exception:
        result["images"] = []
    if dom_image_fallback and not result["images"]:
        try:
            result["images"] = _extract_dom_hero_image(tree, base_url)
        except Exception:
            pass
    try:
        result["videos"] = _extract_json_ld_videos(tree, base_url)
    except Exception:
        result["videos"] = []
    return result
