"""Serper Search — optional paid SERP backend for ``web_search``.

Thin, in-process HTTP client against the Serper.dev Google-SERP API
(https://serper.dev). Serper is **not** required — Chad's default search stack
(self-hosted SearXNG with a Brave fallback) works with zero API keys, matching
the local-first/open-source thesis (see docs/strategy/OPEN_SOURCE_STRATEGY.md).
This tool only activates when the user opts in by setting ``SERPER_API_KEY``;
without it, ``web_search``'s backend chain never includes ``serper_search`` and
nothing changes for users who don't configure it.

Why it exists: self-hosted SearXNG scrapes engines' public search pages with no
proxy/fingerprint infrastructure, so upstream engines (Google/DuckDuckGo/Brave/
Mojeek) block or soft-degrade it under sustained agent traffic (see issue #523).
Serper queries Google directly through managed, non-scraped infrastructure at
~$0.0003-0.001/query — cheap enough to be a practical "unlimited" tier without
the reliability problems of DIY scraping.

Returns the same ``{success, result}`` envelope shape as ``searxng_search``/
``brave_web_search`` (result is a JSON string with a ``web.results[]`` array) so
``web_search._parse_search_results`` consumes it unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool

logger = setup_logger("SerperSearchTool")

_ENDPOINT = "https://google.serper.dev/search"
_TIMEOUT_S = 15.0


def serper_configured() -> bool:
    """True when the optional SERPER_API_KEY secret is set."""
    return bool(ConfigManager.get_secret("SERPER_API_KEY"))


class SerperSearchTool(BaseTool):
    """Query the Serper.dev Google-SERP API and return Brave-shaped search results."""

    name = "serper_search"
    description = (
        "Internal search backend: query the Serper.dev Google-SERP API. Optional, "
        "opt-in via SERPER_API_KEY. Prefer the high-level `web_search` tool instead "
        "of calling this directly."
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
                    "query": {"type": "string", "description": "The search query."},
                    "count": {
                        "type": "integer",
                        "description": "Max results to return (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        q = (params or {}).get("query", "")
        return f'Searching Serper for "{q}"' if q else "Searching Serper"

    def validate_params(self, query: Optional[str] = None, **kwargs: Any) -> tuple[bool, str | None]:
        if not query or not isinstance(query, str) or not query.strip():
            return False, "query must be a non-empty string."
        return True, None

    async def execute(
        self, query: str, count: int = 5, **kwargs: Any
    ) -> Dict[str, Any]:
        api_key = ConfigManager.get_secret("SERPER_API_KEY")
        if not api_key:
            return {"success": False, "error": "SERPER_API_KEY not configured (optional backend)."}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(
                    _ENDPOINT,
                    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                    json={"q": query, "num": max(1, int(count or 5))},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error(f"Serper search failed for {query!r}: {exc}")
            return {"success": False, "error": f"Serper unreachable: {exc}"}

        raw_results = data.get("organic", []) if isinstance(data, dict) else []
        items: List[Dict[str, str]] = []
        for itm in raw_results[: max(1, int(count or 5))]:
            url = itm.get("link")
            if not url:
                continue
            items.append({
                "url": url,
                "title": itm.get("title") or "Untitled",
                "description": itm.get("snippet") or "",
            })

        if not items:
            return {"success": False, "error": "Serper returned no organic results."}

        # Brave-shaped envelope: result is a JSON string with web.results[], which
        # web_search._parse_search_results already knows how to parse.
        payload = json.dumps({"web": {"results": items}})
        return {"success": True, "result": payload}
