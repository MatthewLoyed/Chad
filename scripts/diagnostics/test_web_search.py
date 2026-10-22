"""Integration test: WebSearchTool end-to-end against the real Brave API.

Uses a shim tool_registry whose execute_tool mimics the MCP tools the real registry
exposes (brave_web_search -> Brave API JSON; fetch_html -> httpx GET), so we exercise
the full search -> fetch -> extract pipeline with the real backend.

Run: $env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts\diagnostics\test_web_search.py
"""
from __future__ import annotations

import asyncio
import json

import httpx

from src.shared.config_manager import ConfigManager
from src.tools.web.web_search import WebSearchTool

BRAVE_KEY = ConfigManager.get_secret("BRAVE_API_KEY")
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class ShimRegistry:
    """Mimics the production tool_registry's execute_tool for the two tools web_search uses."""

    async def execute_tool(self, name: str, args: dict) -> dict:
        if name == "brave_web_search":
            return await self._brave(args["query"], args.get("count", 3))
        if name == "fetch_html":
            return await self._fetch(args["url"])
        return {"success": False, "error": f"shim has no tool {name}"}

    async def _brave(self, query: str, count: int) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": BRAVE_KEY, "Accept": "application/json"},
            )
            if r.status_code != 200:
                return {"success": False, "error": f"brave HTTP {r.status_code}: {r.text[:200]}"}
            # Production MCP returns result as a text block containing JSON; mirror that shape.
            return {"success": True, "result": [{"text": json.dumps(r.json())}]}

    async def _fetch(self, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_HEADERS) as c:
                r = await c.get(url)
                if r.status_code != 200:
                    return {"success": False, "error": f"HTTP {r.status_code}"}
                return {"success": True, "result": [{"text": r.text}]}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}


async def main() -> None:
    if not BRAVE_KEY:
        print("No BRAVE_API_KEY; aborting.")
        return

    tool = WebSearchTool()
    registry = ShimRegistry()

    queries = [
        "DeWalt 20V MAX cordless drill specifications",
        "best fireproof document safe box features",
        "Amazon Echo Dot 5th generation features",  # retailer-leaning -> expect some fetch blocks
    ]
    print(f"Running web_search with {len(queries)} queries...\n")
    out = await tool.execute(queries=queries, results_per_query=3, tool_registry=registry)

    print(f"caption: {out.get('caption')}\n")
    for res in out["results"]:
        print("=" * 80)
        print(f"QUERY: {res['query']}  (sources: {res.get('source_count', 0)}, "
              f"fetched ok: {res.get('successful_fetches', 0)})")
        for s in res.get("sources", []):
            status = "OK " if s.get("success") else "ERR"
            note = f"  [{s['error']}]" if s.get("error") else ""
            clen = len(s.get("content", ""))
            print(f"  [{status}] {clen:>5} chars  {s.get('title','')[:50]}{note}")
            print(f"        {s['url']}")
            if s.get("success") and clen:
                print(f"        preview: {s['content'][:200].replace(chr(10), ' ')}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
