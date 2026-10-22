"""Tavily Search — optional paid/free-tier SERP backend for ``web_search``.

Thin, in-process HTTP client against the Tavily Search API (https://tavily.com).
Like ``serper_search``, this is **not** required — Chad's default search stack
(self-hosted SearXNG with a Brave fallback) works with zero API keys. This tool
only activates when the user opts in by setting ``TAVILY_API_KEY``; without it,
``web_search``'s backend chain never includes ``tavily_search``.

Returns the same ``{success, result}`` envelope shape as ``serper_search``/
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

logger = setup_logger("TavilySearchTool")

_ENDPOINT = "https://api.tavily.com/search"
_TIMEOUT_S = 15.0


def tavily_configured() -> bool:
    """True when the optional TAVILY_API_KEY secret is set."""
    return bool(ConfigManager.get_secret("TAVILY_API_KEY"))


_USAGE_ENDPOINT = "https://api.tavily.com/usage"


async def get_tavily_usage() -> Dict[str, Any]:
    """Live quota check via Tavily's GET /usage — does not consume a search credit.

    Unlike Serper/Brave (no equivalent live-check endpoint — see #523 follow-up),
    Tavily exposes this directly, so it's safe to poll from the Settings UI on demand.
    """
    api_key = ConfigManager.get_secret("TAVILY_API_KEY")
    if not api_key:
        return {"success": False, "error": "TAVILY_API_KEY not configured (optional backend)."}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(_USAGE_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error(f"Tavily usage check failed: {exc}")
        return {"success": False, "error": f"Tavily unreachable: {exc}"}

    account = data.get("account", {}) if isinstance(data, dict) else {}
    used = account.get("plan_usage")
    limit = account.get("plan_limit")
    return {
        "success": True,
        "used": used,
        "limit": limit,
        "remaining": (limit - used) if isinstance(used, int) and isinstance(limit, int) else None,
        "plan": account.get("current_plan"),
    }


class TavilySearchTool(BaseTool):
    """Query the Tavily Search API and return Brave-shaped search results."""

    name = "tavily_search"
    description = (
        "Internal search backend: query the Tavily Search API. Optional, opt-in via "
        "TAVILY_API_KEY. Prefer the high-level `web_search` tool instead of calling "
        "this directly."
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
        return f'Searching Tavily for "{q}"' if q else "Searching Tavily"

    def validate_params(self, query: Optional[str] = None, **kwargs: Any) -> tuple[bool, str | None]:
        if not query or not isinstance(query, str) or not query.strip():
            return False, "query must be a non-empty string."
        return True, None

    async def execute(
        self, query: str, count: int = 5, **kwargs: Any
    ) -> Dict[str, Any]:
        api_key = ConfigManager.get_secret("TAVILY_API_KEY")
        if not api_key:
            return {"success": False, "error": "TAVILY_API_KEY not configured (optional backend)."}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(
                    _ENDPOINT,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"query": query, "max_results": max(1, int(count or 5))},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error(f"Tavily search failed for {query!r}: {exc}")
            return {"success": False, "error": f"Tavily unreachable: {exc}"}

        raw_results = data.get("results", []) if isinstance(data, dict) else []
        items: List[Dict[str, str]] = []
        for itm in raw_results[: max(1, int(count or 5))]:
            url = itm.get("url")
            if not url:
                continue
            items.append({
                "url": url,
                "title": itm.get("title") or "Untitled",
                "description": itm.get("content") or "",
            })

        if not items:
            return {"success": False, "error": "Tavily returned no results."}

        # Brave-shaped envelope: result is a JSON string with web.results[], which
        # web_search._parse_search_results already knows how to parse.
        payload = json.dumps({"web": {"results": items}})
        return {"success": True, "result": payload}
