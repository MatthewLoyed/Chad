"""Image search — find licensed / free-to-use images for local HTML and artifacts.

Primary path for websites Chad builds as local ``.html`` files: search → pick a
result URL → embed in ``<img src="...">`` (fine for unpublished local demos) or
``download_file`` into an ``assets/`` folder for a self-contained file.

Providers (tried in order when ``provider=all``):
  1. Openverse — Creative Commons / public domain, **no API key** (always on)
  2. Pexels / Unsplash / Pixabay — optional free API keys
  3. SearXNG ``categories=images`` — keyless fallback via the existing sidecar

This is agent-facing (unlike ``searxng_search`` / ``serper_search`` backends).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool

logger = setup_logger("ImageSearchTool")

_TIMEOUT_S = 15.0
_OPENVERSE_URL = "https://api.openverse.org/v1/images/"
_PEXELS_URL = "https://api.pexels.com/v1/search"
_UNSPLASH_URL = "https://api.unsplash.com/search/photos"
_PIXABAY_URL = "https://pixabay.com/api/"
_BRAVE_IMAGES_URL = "https://api.search.brave.com/res/v1/images/search"
_SERPER_IMAGES_URL = "https://google.serper.dev/images"
_DEFAULT_SEARXNG = "http://127.0.0.1:8080"
_MAX_COUNT = 12
_DEFAULT_COUNT = 6

_USER_AGENT = "ChadAI/1.0 (local assistant; image search for local HTML artifacts)"

_STOCK_PROVIDERS = frozenset({"openverse", "pexels", "unsplash", "pixabay"})
_SEARCH_IMAGE_PROVIDERS = frozenset({"searxng", "brave", "serper"})
_ALL_PROVIDERS = _STOCK_PROVIDERS | _SEARCH_IMAGE_PROVIDERS | frozenset({"all"})


def _pexels_key() -> Optional[str]:
    return ConfigManager.get_secret("PEXELS_API_KEY") or None


def _unsplash_key() -> Optional[str]:
    return (
        ConfigManager.get_secret("UNSPLASH_ACCESS_KEY")
        or ConfigManager.get_secret("UNSPLASH_API_KEY")
        or None
    )


def _pixabay_key() -> Optional[str]:
    return ConfigManager.get_secret("PIXABAY_API_KEY") or None


def _brave_key() -> Optional[str]:
    return ConfigManager.get_secret("BRAVE_API_KEY") or None


def _serper_key() -> Optional[str]:
    return ConfigManager.get_secret("SERPER_API_KEY") or None


def _active_search_image_backends() -> List[str]:
    """Image fallbacks that match Settings → Services ``tools.search_backend``.

    Mirrors ``web_search._backend_chain`` intent: use the active search mode,
    not a hard-coded SearXNG path. Tavily has no image endpoint — when the
    backend is ``tavily``, we fall through to SearXNG only if it is the usual
    local sidecar (still better than inventing a Tavily image call).
    """
    backend = str(ConfigManager.get("tools.search_backend", "searxng")).strip().lower()
    if backend == "brave":
        return ["brave"] if _brave_key() else []
    if backend == "serper":
        return ["serper"] if _serper_key() else []
    if backend == "tavily":
        # No Tavily Images API — use local SearXNG images if present.
        return ["searxng"]
    if backend == "all":
        ordered: List[str] = []
        if _serper_key():
            ordered.append("serper")
        if _brave_key():
            ordered.append("brave")
        return ordered
    # Default / searxng
    return ["searxng"]


def _clamp_count(count: Any) -> int:
    if count is None:
        return _DEFAULT_COUNT
    try:
        return max(1, min(_MAX_COUNT, int(count)))
    except (TypeError, ValueError):
        return _DEFAULT_COUNT


def _normalize_orientation(orientation: Optional[str]) -> Optional[str]:
    if not orientation:
        return None
    value = str(orientation).strip().lower()
    if value in ("landscape", "portrait", "square"):
        return value
    if value in ("any", "all", ""):
        return None
    return None


def _hit(
    *,
    url: str,
    provider: str,
    title: str = "",
    author: str = "",
    license_name: str = "",
    page_url: str = "",
    thumbnail: str = "",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "url": url,
        "thumbnail": thumbnail or url,
        "page_url": page_url or url,
        "title": title or "Untitled",
        "author": author or "",
        "license": license_name or "",
        "provider": provider,
    }
    if width:
        item["width"] = width
    if height:
        item["height"] = height
    return item


async def _search_openverse(
    client: httpx.AsyncClient,
    query: str,
    count: int,
    orientation: Optional[str],
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "q": query,
        "page_size": count,
        "page": 1,
        "mature": "false",
    }
    if orientation:
        params["aspect_ratio"] = orientation
    resp = await client.get(
        _OPENVERSE_URL,
        params=params,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    hits: List[Dict[str, Any]] = []
    for row in results:
        url = row.get("url")
        if not url:
            continue
        license_name = row.get("license") or ""
        version = row.get("license_version") or ""
        if license_name and version:
            license_name = f"CC {license_name.upper()} {version}".replace("CC CC0", "CC0")
        elif license_name:
            license_name = f"CC {license_name}"
        hits.append(
            _hit(
                url=url,
                provider="openverse",
                title=row.get("title") or "",
                author=row.get("creator") or "",
                license_name=license_name or "Creative Commons / public domain",
                page_url=row.get("foreign_landing_url") or row.get("detail_url") or "",
                thumbnail=row.get("thumbnail") or url,
                width=row.get("width"),
                height=row.get("height"),
            )
        )
    return hits


async def _search_pexels(
    client: httpx.AsyncClient,
    query: str,
    count: int,
    orientation: Optional[str],
    api_key: str,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"query": query, "per_page": count}
    if orientation:
        params["orientation"] = orientation
    resp = await client.get(
        _PEXELS_URL,
        params=params,
        headers={"Authorization": api_key, "User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    data = resp.json()
    hits: List[Dict[str, Any]] = []
    for row in data.get("photos") or []:
        src = row.get("src") or {}
        url = src.get("large") or src.get("original") or src.get("medium")
        if not url:
            continue
        photographer = row.get("photographer") or ""
        hits.append(
            _hit(
                url=url,
                provider="pexels",
                title=row.get("alt") or f"Photo by {photographer}",
                author=photographer,
                license_name="Pexels License (free use)",
                page_url=row.get("url") or "",
                thumbnail=src.get("tiny") or src.get("small") or url,
                width=row.get("width"),
                height=row.get("height"),
            )
        )
    return hits


async def _search_unsplash(
    client: httpx.AsyncClient,
    query: str,
    count: int,
    orientation: Optional[str],
    api_key: str,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"query": query, "per_page": count}
    if orientation:
        params["orientation"] = orientation
    resp = await client.get(
        _UNSPLASH_URL,
        params=params,
        headers={
            "Authorization": f"Client-ID {api_key}",
            "User-Agent": _USER_AGENT,
            "Accept-Version": "v1",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    hits: List[Dict[str, Any]] = []
    for row in data.get("results") or []:
        urls = row.get("urls") or {}
        url = urls.get("regular") or urls.get("full") or urls.get("small")
        if not url:
            continue
        user = row.get("user") or {}
        links = row.get("links") or {}
        hits.append(
            _hit(
                url=url,
                provider="unsplash",
                title=row.get("description") or row.get("alt_description") or "Unsplash photo",
                author=user.get("name") or user.get("username") or "",
                license_name="Unsplash License (free use)",
                page_url=links.get("html") or "",
                thumbnail=urls.get("thumb") or urls.get("small") or url,
                width=row.get("width"),
                height=row.get("height"),
            )
        )
    return hits


async def _search_pixabay(
    client: httpx.AsyncClient,
    query: str,
    count: int,
    orientation: Optional[str],
    api_key: str,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "key": api_key,
        "q": query,
        "image_type": "all",
        "safesearch": "true",
        "per_page": max(3, count),  # Pixabay minimum is 3
    }
    if orientation == "landscape":
        params["orientation"] = "horizontal"
    elif orientation == "portrait":
        params["orientation"] = "vertical"
    resp = await client.get(
        _PIXABAY_URL,
        params=params,
        headers={"User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    data = resp.json()
    hits: List[Dict[str, Any]] = []
    for row in (data.get("hits") or [])[:count]:
        url = row.get("largeImageURL") or row.get("webformatURL")
        if not url:
            continue
        hits.append(
            _hit(
                url=url,
                provider="pixabay",
                title=row.get("tags") or "Pixabay image",
                author=row.get("user") or "",
                license_name="Pixabay Content License (download; do not hotlink if publishing)",
                page_url=row.get("pageURL") or "",
                thumbnail=row.get("previewURL") or url,
                width=row.get("imageWidth"),
                height=row.get("imageHeight"),
            )
        )
    return hits


async def _search_brave(
    client: httpx.AsyncClient,
    query: str,
    count: int,
    api_key: str,
) -> List[Dict[str, Any]]:
    resp = await client.get(
        _BRAVE_IMAGES_URL,
        params={"q": query, "count": count, "safesearch": "strict"},
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
            "User-Agent": _USER_AGENT,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    hits: List[Dict[str, Any]] = []
    for row in data.get("results") or []:
        props = row.get("properties") or {}
        url = props.get("url") or row.get("url")
        if not url:
            continue
        thumb = (row.get("thumbnail") or {}).get("src") or url
        hits.append(
            _hit(
                url=url,
                provider="brave",
                title=row.get("title") or "Brave image",
                author="",
                license_name="Unknown (Brave image search — prefer Openverse/stock for redistribution)",
                page_url=(row.get("meta_url") or {}).get("url") or row.get("url") or "",
                thumbnail=thumb,
            )
        )
    return hits


async def _search_serper(
    client: httpx.AsyncClient,
    query: str,
    count: int,
    api_key: str,
) -> List[Dict[str, Any]]:
    resp = await client.post(
        _SERPER_IMAGES_URL,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json", "User-Agent": _USER_AGENT},
        json={"q": query, "num": count},
    )
    resp.raise_for_status()
    data = resp.json()
    hits: List[Dict[str, Any]] = []
    for row in data.get("images") or []:
        url = row.get("imageUrl")
        if not url:
            continue
        hits.append(
            _hit(
                url=url,
                provider="serper",
                title=row.get("title") or "Google image",
                author="",
                license_name="Unknown (Serper/Google Images — prefer Openverse/stock for redistribution)",
                page_url=row.get("link") or "",
                thumbnail=row.get("thumbnailUrl") or url,
                width=row.get("imageWidth"),
                height=row.get("imageHeight"),
            )
        )
    return hits


async def _search_searxng(
    client: httpx.AsyncClient,
    query: str,
    count: int,
) -> List[Dict[str, Any]]:
    base = str(ConfigManager.get("tools.searxng_url", _DEFAULT_SEARXNG)).rstrip("/")
    resp = await client.get(
        f"{base}/search",
        params={
            "q": query,
            "format": "json",
            "categories": "images",
            "safesearch": "1",
        },
        headers={"User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    data = resp.json()
    hits: List[Dict[str, Any]] = []
    for row in (data.get("results") or [])[:count]:
        # SearXNG image results: img_src is the image; url is often the page.
        url = row.get("img_src") or row.get("thumbnail_src") or row.get("url")
        if not url or not str(url).startswith(("http://", "https://")):
            continue
        hits.append(
            _hit(
                url=url,
                provider="searxng",
                title=row.get("title") or "Image result",
                author="",
                license_name="Unknown (SearXNG web image — prefer Openverse/stock for redistribution)",
                page_url=row.get("url") or url,
                thumbnail=row.get("thumbnail_src") or url,
            )
        )
    return hits


class ImageSearchTool(BaseTool):
    """Search Openverse / stock APIs / active web-search backend for local HTML images."""

    name = "search_images"
    description = (
        "Search for images to use in local HTML websites and visual artifacts. "
        "Returns direct image URLs plus attribution/license. For unpublished local "
        "HTML demos, embed result `url` values in <img src>. For self-contained "
        "files, download_file the chosen url into an assets/ folder first. "
        "Order: Openverse (CC, no key) → optional Pexels/Unsplash/Pixabay keys → "
        "then the active Settings→Services search backend's image mode "
        "(SearXNG / Brave / Serper — same as web_search). "
        "Prefer this over refusing image work for local demos."
    )
    category = "web"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Image search query in English "
                            "(e.g. 'anime aesthetic city night', 'sushi platter')."
                        ),
                    },
                    "count": {
                        "type": "integer",
                        "description": f"Max results to return (default {_DEFAULT_COUNT}, max {_MAX_COUNT}).",
                        "default": _DEFAULT_COUNT,
                    },
                    "orientation": {
                        "type": "string",
                        "description": "Optional: landscape, portrait, square, or any.",
                        "enum": ["landscape", "portrait", "square", "any"],
                    },
                    "provider": {
                        "type": "string",
                        "description": (
                            "openverse | pexels | unsplash | pixabay | searxng | brave | "
                            "serper | all (default all — Openverse/stock then active search backend)."
                        ),
                        "enum": [
                            "all",
                            "openverse",
                            "pexels",
                            "unsplash",
                            "pixabay",
                            "searxng",
                            "brave",
                            "serper",
                        ],
                        "default": "all",
                    },
                },
                "required": ["query"],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        q = (params or {}).get("query", "")
        return f'Searching images for "{q}"' if q else "Searching images"

    def validate_params(self, query: Optional[str] = None, **kwargs: Any) -> tuple[bool, str | None]:
        if not query or not isinstance(query, str) or not query.strip():
            return False, "query must be a non-empty string."
        return True, None

    async def execute(
        self,
        query: str,
        count: int = _DEFAULT_COUNT,
        orientation: Optional[str] = None,
        provider: str = "all",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "query must be a non-empty string."}

        n = _clamp_count(count)
        orient = _normalize_orientation(orientation)
        provider_key = (provider or "all").strip().lower()
        if provider_key not in _ALL_PROVIDERS:
            provider_key = "all"

        errors: List[str] = []
        collected: List[Dict[str, Any]] = []
        used_providers: List[str] = []
        active_backends = _active_search_image_backends()

        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
            async def run(name: str, coro) -> None:
                nonlocal collected
                if len(collected) >= n:
                    return
                try:
                    hits = await coro
                    if hits:
                        used_providers.append(name)
                        for hit in hits:
                            if len(collected) >= n:
                                break
                            if any(existing["url"] == hit["url"] for existing in collected):
                                continue
                            collected.append(hit)
                except Exception as exc:
                    logger.warning(f"image search provider {name} failed: {exc}")
                    errors.append(f"{name}: {exc}")

            async def run_search_backend(name: str) -> None:
                if name == "searxng":
                    await run("searxng", _search_searxng(client, query, n))
                elif name == "brave":
                    key = _brave_key()
                    if key:
                        await run("brave", _search_brave(client, query, n, key))
                    else:
                        errors.append("brave: BRAVE_API_KEY not configured")
                elif name == "serper":
                    key = _serper_key()
                    if key:
                        await run("serper", _search_serper(client, query, n, key))
                    else:
                        errors.append("serper: SERPER_API_KEY not configured")

            if provider_key == "all":
                await run("openverse", _search_openverse(client, query, n, orient))

                pexels = _pexels_key()
                if pexels:
                    await run("pexels", _search_pexels(client, query, n, orient, pexels))
                unsplash = _unsplash_key()
                if unsplash:
                    await run("unsplash", _search_unsplash(client, query, n, orient, unsplash))
                pixabay = _pixabay_key()
                if pixabay:
                    await run("pixabay", _search_pixabay(client, query, n, orient, pixabay))

                # Fill remaining slots from the active web-search backend's image mode.
                if len(collected) < n:
                    for backend_name in active_backends:
                        if len(collected) >= n:
                            break
                        await run_search_backend(backend_name)
            elif provider_key in _STOCK_PROVIDERS:
                if provider_key == "openverse":
                    await run("openverse", _search_openverse(client, query, n, orient))
                elif provider_key == "pexels":
                    key = _pexels_key()
                    if key:
                        await run("pexels", _search_pexels(client, query, n, orient, key))
                    else:
                        errors.append("pexels: PEXELS_API_KEY not configured")
                elif provider_key == "unsplash":
                    key = _unsplash_key()
                    if key:
                        await run("unsplash", _search_unsplash(client, query, n, orient, key))
                    else:
                        errors.append("unsplash: UNSPLASH_ACCESS_KEY not configured")
                elif provider_key == "pixabay":
                    key = _pixabay_key()
                    if key:
                        await run("pixabay", _search_pixabay(client, query, n, orient, key))
                    else:
                        errors.append("pixabay: PIXABAY_API_KEY not configured")
            else:
                # Explicit search-backend provider (searxng / brave / serper)
                await run_search_backend(provider_key)

        if not collected:
            detail = "; ".join(errors) if errors else "no results"
            mode = ", ".join(active_backends) or "none"
            hint = (
                "Openverse needs network access. Optional stock keys: PEXELS_API_KEY / "
                "UNSPLASH_ACCESS_KEY / PIXABAY_API_KEY. Active search-image fallback: "
                f"{mode} (Settings → Services search backend)."
            )
            return {
                "success": False,
                "error": f"No images found for {query!r} ({detail}). {hint}",
            }

        payload = {
            "query": query,
            "count": len(collected),
            "providers_used": used_providers,
            "active_search_backends": active_backends,
            "images": collected,
            "usage": (
                "For local unpublished HTML: use images[].url in <img src>. "
                "For a self-contained file: download_file(url, destination_path) into assets/, "
                "then reference the local path. Prefer Openverse/Pexels/Unsplash licenses when "
                "the site may later be published."
            ),
        }
        if errors:
            payload["provider_warnings"] = errors

        lines = [
            f"Found {len(collected)} image(s) for {query!r} via {', '.join(used_providers)}.",
            "Embed `url` in local HTML, or download_file first for offline assets.",
            "",
        ]
        for i, img in enumerate(collected, 1):
            lic = f" | {img['license']}" if img.get("license") else ""
            author = f" — {img['author']}" if img.get("author") else ""
            lines.append(
                f"{i}. [{img['provider']}] {img['title']}{author}{lic}\n"
                f"   url: {img['url']}"
            )

        return {
            "success": True,
            "result": "\n".join(lines),
            "images": collected,
            "data": payload,
            "result_json": json.dumps(payload),
        }
