"""
SearXNG Search — self-hosted metasearch backend for ``web_search``.

Thin, in-process HTTP client against a locally-hosted SearXNG instance
(``docker/searxng/``). SearXNG aggregates Google/Bing/DuckDuckGo/Brave/Mojeek/etc.
behind one JSON endpoint with no API key and no per-month quota, which is what
lets ``web_search`` run batches of queries per turn (see ``tools.web_search_max_queries``).

This tool is *not* agent-facing on its own — ``web_search`` dispatches to it via
``registry.execute_tool("searxng_search", ...)``. It returns the same
``{success, result}`` envelope shape as ``brave_web_search`` (result is a JSON
string with a ``web.results[]`` array) so ``web_search._parse_search_results``
consumes it unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool

logger = setup_logger("SearxngSearchTool")

_DEFAULT_URL = "http://127.0.0.1:8080"
_TIMEOUT_S = 15.0


class SearxngSearchTool(BaseTool):
    """Query a self-hosted SearXNG instance and return Brave-shaped search results."""

    name = "searxng_search"
    description = (
        "Internal search backend: query a self-hosted SearXNG metasearch instance. "
        "Prefer the high-level `web_search` tool instead of calling this directly."
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
        return f'Searching SearXNG for "{q}"' if q else "Searching SearXNG"

    def validate_params(self, query: Optional[str] = None, **kwargs: Any) -> tuple[bool, str | None]:
        if not query or not isinstance(query, str) or not query.strip():
            return False, "query must be a non-empty string."
        return True, None

    async def execute(
        self, query: str, count: int = 5, **kwargs: Any
    ) -> Dict[str, Any]:
        base = str(ConfigManager.get("tools.searxng_url", _DEFAULT_URL)).rstrip("/")
        params = {
            "q": query,
            "format": "json",
            "safesearch": "0",
            # 'general' category covers web text results; SearXNG merges across engines.
            "categories": "general",
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(f"{base}/search", params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error(f"SearXNG search failed for {query!r}: {exc}")
            return {"success": False, "error": f"SearXNG unreachable: {exc}"}

        raw_results = data.get("results", []) if isinstance(data, dict) else []
        items: List[Dict[str, str]] = []
        for itm in raw_results[: max(1, int(count or 5))]:
            url = itm.get("url")
            if not url:
                continue
            items.append({
                "url": url,
                "title": itm.get("title") or "Untitled",
                # SearXNG calls the snippet 'content'; map it to Brave's 'description'.
                "description": itm.get("content") or "",
            })

        if not items:
            unresp = data.get("unresponsive_engines") if isinstance(data, dict) else None
            detail = "SearXNG returned no results."
            if unresp:
                detail += " Engines: " + ", ".join(
                    f"{name} ({reason})" for name, reason in unresp[:4]
                )
            return {"success": False, "error": detail, "searxng_empty": True}

        # Brave-shaped envelope: result is a JSON string with web.results[], which
        # web_search._parse_search_results already knows how to parse.
        payload = json.dumps({"web": {"results": items}})
        return {"success": True, "result": payload}
