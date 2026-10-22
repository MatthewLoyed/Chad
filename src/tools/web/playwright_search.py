"""
Playwright search — headless browser fallback when SearXNG JSON returns no hits.

Uses the same shared Chromium pool as ``playwright_fetch`` (page fetch Tier-2), not the
Playwright MCP / browser_agent stack. Scrapes Startpage HTML when the curated SearXNG
engines (Startpage + Wikipedia) cannot satisfy a query via JSON (e.g. Startpage CAPTCHA).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool
from src.tools.web.playwright_fetch import playwright_fallback_enabled, run_with_page

logger = setup_logger("PlaywrightSearchTool")

_STARTPAGE_SEARCH = "https://www.startpage.com/sp/search?query={query}"

_EXTRACT_JS = """
() => {
  const blocked = /startpage\\.com|google\\.com|bing\\.com|duckduckgo\\.com/i;
  const items = [];
  const seen = new Set();
  const blocks = document.querySelectorAll(
    'article, [class*="result"], [data-testid*="result"]'
  );
  for (const block of blocks) {
    const link = block.querySelector('a[href^="http"]');
    if (!link) continue;
    const url = link.href;
    if (!url || blocked.test(url)) continue;
    const titleEl = block.querySelector('h2, h3, [class*="title"]');
    const title = (titleEl?.innerText || link.innerText || '').trim();
    const snippetEl = block.querySelector('p, [class*="desc"], [class*="snippet"]');
    const description = (snippetEl?.innerText || '').trim();
    if (!title || title.length < 4 || seen.has(url)) continue;
    seen.add(url);
    items.push({ url, title, description });
  }
  if (items.length === 0) {
    for (const link of document.querySelectorAll('a[href^="http"]')) {
      const url = link.href;
      if (!url || blocked.test(url)) continue;
      const title = (link.innerText || '').trim();
      if (!title || title.length < 8 || seen.has(url)) continue;
      seen.add(url);
      items.push({ url, title, description: '' });
      if (items.length >= 10) break;
    }
  }
  return items;
}
"""


def playwright_search_fallback_enabled() -> bool:
    raw = ConfigManager.get("tools.web_search_playwright_search_fallback", True)
    return bool(raw) and playwright_fallback_enabled()


class PlaywrightSearchTool(BaseTool):
    """Headless Startpage scrape — internal fallback for ``web_search``."""

    name = "playwright_search"
    description = (
        "Internal search fallback: render Startpage in headless Chromium when SearXNG "
        "JSON returns no results. Prefer the high-level `web_search` tool."
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
        return f'Searching via Playwright (Startpage) for "{q}"' if q else "Playwright search"

    def validate_params(self, query: Optional[str] = None, **kwargs: Any) -> tuple[bool, str | None]:
        if not query or not isinstance(query, str) or not query.strip():
            return False, "query must be a non-empty string."
        return True, None

    async def execute(self, query: str, count: int = 5, **kwargs: Any) -> Dict[str, Any]:
        if not playwright_search_fallback_enabled():
            return {"success": False, "error": "Playwright search fallback is disabled."}

        target = _STARTPAGE_SEARCH.format(query=quote_plus(query.strip()))

        async def _go(page):
            resp = await page.goto(target, wait_until="domcontentloaded", timeout=25_000)
            if resp and resp.status >= 400:
                raise RuntimeError(f"Playwright search HTTP {resp.status}")
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            return await page.evaluate(_EXTRACT_JS)

        try:
            raw_items = await run_with_page(_go)
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.error(f"Playwright search failed for {query!r}: {exc}")
            return {"success": False, "error": f"Playwright search failed: {exc}"}

        items: List[Dict[str, str]] = []
        for itm in raw_items or []:
            url = itm.get("url")
            if not url:
                continue
            items.append({
                "url": url,
                "title": itm.get("title") or "Untitled",
                "description": itm.get("description") or "",
            })
            if len(items) >= max(1, int(count or 5)):
                break

        if not items:
            return {
                "success": False,
                "error": "Playwright search returned no parseable results.",
                "playwright_empty": True,
            }

        payload = json.dumps({"web": {"results": items}})
        return {"success": True, "result": payload, "search_backend": "playwright_search"}
