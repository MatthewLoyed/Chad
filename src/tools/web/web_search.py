"""
Web Search — programmatic web lookup and batch research.

Takes one or many queries and, for each, runs a search, fetches the top result
pages, and extracts clean article text. Callers pass queries and get back ranked,
readable content per query — no raw HTML juggling.

Pipeline (all in code, no model in the search loop):
    search (SearXNG -> Playwright Startpage -> Serper [optional, opt-in] -> Brave
    fallback)  ->  pick top URLs  ->  fetch pages (fetch_html -> Playwright ->
    snippet fallback)  ->  markdown extract

Results that come back non-empty but share no meaningful term with the query
(a scraped engine's "soft degradation" failure mode, see issue #523) are treated
as a miss and the chain keeps going — see ``_looks_relevant``.

Config (``config/config.yaml`` → ``tools.*``):
    search_backend, searxng_url, web_search_max_queries, web_search_results_per_query

See ``docs/features/WEB_SEARCH.md`` for routing (``web_search`` vs research_agent).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.core.tools.formatter import tool_display
from src.tools.web.content_extraction import DEFAULT_MAX_CHARS
from src.tools.web.fetch_pipeline import resolve_url_content
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool

logger = setup_logger("WebSearchTool")

# Per-page fetch ceiling so one slow host can't stall the whole batch.
_FETCH_TIMEOUT_S = 30.0
# Trim each page's extracted text so a handful of long pages don't blow the agent's context.
_MAX_CONTENT_CHARS = DEFAULT_MAX_CHARS
# Minimum spacing between search calls. Brave's free tier allows ~1 req/sec, so searches
# are serialized with this gap (fetches stay parallel). A self-hosted SearXNG backend has
# no such limit, so its spacing is 0 (see _backend_chain). Retries cover transient blips.
_SEARCH_MIN_INTERVAL_BRAVE_S = 1.1
_SEARCH_MIN_INTERVAL_SEARXNG_S = 0.0
_SEARCH_RETRIES = 2


def _max_queries() -> int:
    raw = ConfigManager.get("tools.web_search_max_queries", 10)
    try:
        return max(1, min(50, int(raw)))
    except (TypeError, ValueError):
        return 10


def _default_results_per_query() -> int:
    raw = ConfigManager.get("tools.web_search_results_per_query", 3)
    try:
        return max(1, min(5, int(raw)))
    except (TypeError, ValueError):
        return 3


def _brave_configured() -> bool:
    return bool(ConfigManager.get_secret("BRAVE_API_KEY"))


def _optional_paid_backends() -> list[tuple[str, "Any"]]:
    """(tool_name, is_configured) pairs for paid/free-tier search APIs, in preference order.

    Add a new backend by adding one entry here (plus its client module) — the
    round-robin in ``_backend_chain`` (``search_backend: all``) picks it up automatically.
    """
    from src.tools.web.serper_search import serper_configured
    from src.tools.web.tavily_search import tavily_configured

    return [
        ("serper_search", serper_configured),
        ("tavily_search", tavily_configured),
        ("brave_web_search", _brave_configured),
    ]


_round_robin_counter = itertools.count()


def _backend_chain() -> tuple[list[str], float]:
    """Return (ordered search tool names to try, gate interval) from config.

    ``tools.search_backend`` values:
      - ``searxng`` (default): SearXNG JSON → Playwright Startpage scrape → whichever
        paid/free-tier API backends have keys configured, in that fixed order.
      - ``brave`` / ``serper`` / ``tavily``: that single backend only, nothing else.
      - ``all``: **API keys only, no SearXNG** — round-robins across whichever of
        Serper/Tavily/Brave have keys configured, alternating which one goes first
        each call so usage spreads across quotas instead of always hitting the same
        one. SearXNG only runs if the user explicitly selects it (``searxng``);
        ``all`` never falls back to it.

    Every paid/free-tier backend is opt-in — it only joins a chain when its key is
    configured; the default (``searxng``) chain is unchanged for anyone who hasn't set
    a key (see docs/strategy/OPEN_SOURCE_STRATEGY.md).
    """
    from src.tools.web.playwright_search import playwright_search_fallback_enabled

    backend = str(ConfigManager.get("tools.search_backend", "brave")).strip().lower()
    if backend == "searxng":
        chain = ["searxng_search"]
        if playwright_search_fallback_enabled():
            chain.append("playwright_search")
        chain.extend(name for name, is_configured in _optional_paid_backends() if is_configured())
        return chain, _SEARCH_MIN_INTERVAL_SEARXNG_S
    if backend == "all":
        paid = [name for name, is_configured in _optional_paid_backends() if is_configured()]
        if not paid:
            return [], _SEARCH_MIN_INTERVAL_SEARXNG_S
        offset = next(_round_robin_counter) % len(paid)
        return paid[offset:] + paid[:offset], _SEARCH_MIN_INTERVAL_SEARXNG_S
    if backend in {"serper", "tavily"}:
        return [f"{backend}_search"], _SEARCH_MIN_INTERVAL_SEARXNG_S
    return ["brave_web_search"], _SEARCH_MIN_INTERVAL_BRAVE_S


class _SearchGate:
    """Serializes search calls with a minimum interval between them (rate-limit guard)."""

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval = min_interval_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last = loop.time()


class SearchBudget:
    """Shared, task-scoped counter of remaining search results (issue #524).

    One instance is created per deep-research task (see search_budget_var in
    src/core/orchestration/context.py) and shared by every parallel worker via
    asyncio's automatic ContextVar copy-on-task-creation, so concurrent workers
    draining the same pool need atomic check-and-decrement — hence the lock,
    same shape as _SearchGate above.
    """

    def __init__(self, total: int) -> None:
        self._lock = asyncio.Lock()
        self._remaining = max(0, total)
        self._total = max(0, total)

    @property
    def remaining(self) -> int:
        return self._remaining

    async def try_consume(self, n: int) -> tuple[bool, int]:
        """Atomically decrement by up to n. Returns (had_budget, remaining_after)."""
        async with self._lock:
            if self._remaining <= 0:
                return False, 0
            self._remaining = max(0, self._remaining - max(0, n))
            return True, self._remaining


_SCRAPED_BACKENDS = {"searxng_search", "playwright_search"}

_STOPWORDS = {
    "the", "and", "for", "with", "near", "best", "top", "what", "where", "who",
    "how", "does", "that", "this", "from", "your", "about", "into", "over",
}


def _looks_relevant(query: str, hits: List[Dict[str, str]]) -> bool:
    """True unless every hit matches at most one loose keyword from a multi-term query.

    Guards against the "soft-degraded" failure mode where a backend returns
    ``success: true`` with real-looking but unrelated results — e.g. a scraped engine
    treating a business name as loose OR-matched keywords and returning a dictionary
    definition or Wikipedia trivia page for just one of the words instead of anything
    about the business (see issue #523: "Dream Events & Catering" -> a Wikipedia page
    about a YouTuber named "Dream"). A plain single-token overlap check can't catch
    this, since that's exactly how the garbage was produced — it always shares at
    least one word. Require a majority of the distinctive query terms to land in one
    hit instead. Only proper nouns/distinctive words count, so generic terms
    (stopwords) can't inflate the match count.
    """
    terms = {w for w in re.findall(r"[a-z0-9']{4,}", query.lower()) if w not in _STOPWORDS}
    if not terms:
        return True  # too generic a query to judge; don't block on it
    # For a 1-2 term query any single match is meaningful; longer queries need a
    # majority (at least half, floor 2) so a lone incidental word doesn't pass.
    threshold = 1 if len(terms) <= 2 else max(2, (len(terms) + 1) // 2)
    for hit in hits:
        haystack = f"{hit.get('title', '')} {hit.get('description', '')} {hit.get('url', '')}".lower()
        if sum(1 for term in terms if term in haystack) >= threshold:
            return True
    return False


def _parse_search_results(search_payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract [{url, title, description}] from a brave_web_search payload.

    Brave's MCP server returns plain-text blocks; the raw API returns JSON. Handle
    both, plus a markdown-link fallback, then dedupe and drop search-engine clutter.
    """
    if not search_payload.get("success"):
        logger.debug(f"Search payload reported failure: {search_payload.get('error')}")
        return []

    raw = search_payload.get("result")
    if not raw:
        return []

    text = ""
    if isinstance(raw, list):
        for part in raw:
            text += str(part.get("text")) if isinstance(part, dict) and "text" in part else str(part)
    else:
        text = str(raw)

    items: List[Dict[str, str]] = []

    # Strategy 1: JSON (raw Brave API or JSON-in-text MCP responses).
    try:
        inner = text.strip()
        if inner.startswith("```json"):
            inner = re.sub(r"^```json\s*", "", inner)
            inner = re.sub(r"\s*```$", "", inner)
        parsed = json.loads(inner)
        web_block = parsed.get("web", {}) if isinstance(parsed, dict) else {}
        raw_items = web_block.get("results", []) if isinstance(web_block, dict) else []
        if not raw_items and isinstance(parsed, dict) and "results" in parsed:
            raw_items = parsed["results"]
        if not raw_items and isinstance(parsed, list):
            raw_items = parsed
        for itm in raw_items or []:
            if isinstance(itm, dict) and itm.get("url"):
                items.append({
                    "url": itm["url"],
                    "title": itm.get("title") or itm.get("meta_url") or "Untitled",
                    "description": itm.get("description") or "",
                })
    except Exception:
        pass

    # Strategy 2: Brave MCP plain-text blocks ("Title: ...\nURL: ...\nDescription: ...").
    if not items:
        for block in re.split(r"\n\s*\n", text):
            title = url = desc = ""
            for line in block.splitlines():
                line = line.strip()
                if line.lower().startswith("title:"):
                    title = line[6:].strip()
                elif line.lower().startswith("url:"):
                    url = line[4:].strip()
                elif line.lower().startswith("description:"):
                    desc = line[12:].strip()
            if url.startswith("http"):
                items.append({"url": url, "title": title or "Untitled", "description": desc})

    # Strategy 3: markdown links.
    if not items:
        for m in re.finditer(r"\[([^\]]+)\]\((https?://[^\)]+)\)", text):
            title, url = m.groups()
            items.append({"url": url, "title": title.strip(), "description": ""})

    seen: set[str] = set()
    unique: List[Dict[str, str]] = []
    for entry in items:
        u = entry["url"]
        if "brave.com" in u or "google.com" in u or u in seen:
            continue
        seen.add(u)
        unique.append(entry)
    return unique


@tool_display("Searching the web...")
class WebSearchTool(BaseTool):
    """Search the web for one or many queries and return clean page content per query."""

    name = "web_search"
    description = (
        "Search the web and get back clean, readable page content — not just snippets. "
        "Pass one query for a quick lookup or many at once (parallel batch). Each query "
        "returns top sources with extracted article text. Backend: curated SearXNG "
        "(Startpage + Wikipedia) with Playwright Startpage and Brave fallbacks. "
        "Never invent URLs or facts beyond what is returned. "
        "Supervisor: use for quick 1–2 query lookups; delegate multi-item deep research "
        "to research_agent. For YouTube discovery use `site:youtube.com <topic>` with "
        "`fetch_content=false` to get watch URLs and titles. "
        "Sources may carry a `media` field ({images: [...], videos: [...]}) when the page "
        "exposed og:image/JSON-LD video metadata — the UI renders these automatically, no "
        "action needed to display them. If a video is central to the answer, call "
        "show_youtube_video with the exact title/url from a youtube-platform `media.videos` "
        "entry for a full inline player."
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
                    "queries": {
                        "type": "array",
                        "description": (
                            "One or more focused search queries. Provide several at once "
                            "(e.g. one per item you're researching) — they run in parallel."
                        ),
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "results_per_query": {
                        "type": "integer",
                        "description": (
                            f"Top pages to fetch+extract per query (1-5, default "
                            f"{_default_results_per_query()} from config)."
                        ),
                        "default": _default_results_per_query(),
                    },
                    "fetch_content": {
                        "type": "boolean",
                        "description": (
                            "Fetch and extract full page text (default true). Set false for a "
                            "fast links+snippets-only pass."
                        ),
                        "default": True,
                    },
                },
                "required": ["queries"],
            },
        }

    def validate_params(
        self, queries: Optional[List[str]] = None, **kwargs: Any
    ) -> tuple[bool, str | None]:
        if not queries or not isinstance(queries, list):
            return False, "queries must be a non-empty array of strings."
        cap = _max_queries()
        if len(queries) > cap:
            return False, f"Maximum {cap} queries per call (tools.web_search_max_queries)."
        for i, q in enumerate(queries):
            if not isinstance(q, str) or not q.strip():
                return False, f"queries[{i}] must be a non-empty string."
        return True, None

    async def execute(
        self,
        queries: Optional[List[str]] = None,
        query: Optional[str] = None,
        results_per_query: Optional[int] = None,
        fetch_content: bool = True,
        tool_registry: Any = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if queries is None:
            if query:
                queries = [query]
            else:
                queries = []

        if not queries:
            return {"success": False, "error": "queries must be a non-empty array of strings."}

        if results_per_query is None and limit is not None:
            results_per_query = limit

        if not tool_registry:
            try:
                from src.infrastructure.tools import ToolRegistry
                tool_registry = ToolRegistry()
            except Exception:
                tool_registry = None

        if not tool_registry:
            return {"success": False, "error": "web_search requires tool_registry (internal error)."}

        from src.core.orchestration.context import search_budget_var
        budget = search_budget_var.get()
        if budget is not None and budget.remaining <= 0:
            return {
                "success": False,
                "error": (
                    "Search budget exhausted for this research task "
                    "(0 results remaining) — synthesize with what you already have."
                ),
            }

        per_query = max(1, min(5, int(results_per_query or _default_results_per_query())))
        logger.info(
            f"[ SEARCH ] {len(queries)} queries (results_per_query={per_query}, "
            f"fetch_content={fetch_content})"
        )

        backends, interval = _backend_chain()
        gate = _SearchGate(interval)

        results = await asyncio.gather(
            *[self._run_query(tool_registry, q.strip(), per_query, fetch_content, gate, backends)
              for q in queries]
        )

        if budget is not None:
            total_new_results = sum(r.get("source_count", 0) for r in results)
            await budget.try_consume(total_new_results)

        found = sum(1 for r in results if r.get("source_count", 0) > 0)
        searxng_failures = sum(1 for r in results if r.get("searxng_failed"))
        playwright_fallbacks = sum(
            1 for r in results if r.get("search_backend") == "playwright_search"
        )
        brave_fallbacks = sum(1 for r in results if r.get("search_backend") == "brave_web_search")
        searxng_primary = bool(backends) and backends[0] == "searxng_search"
        searxng_connected = searxng_primary and searxng_failures == 0

        warnings: List[str] = []
        if searxng_failures:
            warnings.append(
                f"SearXNG JSON returned no results for {searxng_failures}/{len(queries)} "
                f"queries (curated engines; CAPTCHA/blocking is common)."
            )
            if playwright_fallbacks:
                warnings.append(
                    f"Playwright Startpage fallback used for {playwright_fallbacks}/{len(queries)} queries."
                )
            if brave_fallbacks:
                warnings.append(
                    f"Brave API fallback used for {brave_fallbacks}/{len(queries)} queries."
                )
            elif found == 0:
                warnings.append(
                    "No search backend returned results. Check Docker (chad-searxng) in "
                    "Settings → Services and E2B/Playwright availability."
                )
        elif playwright_fallbacks:
            warnings.append(
                f"Playwright Startpage fallback used for {playwright_fallbacks}/{len(queries)} queries."
            )
        elif brave_fallbacks:
            warnings.append(f"Brave fallback used for {brave_fallbacks}/{len(queries)} queries.")

        if found == 0:
            search_status = "unavailable"
            caption = (
                f"Web search FAILED: 0/{len(queries)} queries returned content. "
                + (warnings[0] if warnings else "No search backend returned results.")
            )
            return {
                "success": False,
                "error": caption,
                "search_status": search_status,
                "searxng_connected": searxng_connected,
                "query_count": len(queries),
                "results": results,
                "caption": caption,
                "warnings": warnings or None,
                "searxng_failures": searxng_failures or None,
                "playwright_fallbacks": playwright_fallbacks or None,
                "brave_fallbacks": brave_fallbacks or None,
            }

        if searxng_failures:
            search_status = "degraded"
        else:
            search_status = "ok"

        caption = f"Web search complete: {found}/{len(queries)} queries returned content."
        if warnings:
            caption += " " + " ".join(warnings)

        return {
            "success": True,
            "search_status": search_status,
            "searxng_connected": searxng_connected,
            "query_count": len(queries),
            "results": results,
            "caption": caption,
            "warnings": warnings or None,
            "searxng_failures": searxng_failures or None,
            "playwright_fallbacks": playwright_fallbacks or None,
            "brave_fallbacks": brave_fallbacks or None,
        }

    async def _run_query(
        self, registry: Any, query: str, per_query: int, fetch_content: bool,
        gate: "_SearchGate", backends: List[str],
    ) -> Dict[str, Any]:
        hits: List[Dict[str, str]] = []
        last_err: Optional[str] = None
        search_backend: Optional[str] = None
        searxng_failed = False
        weak_hits: List[Dict[str, str]] = []
        weak_backend: Optional[str] = None

        for backend in backends:
            for attempt in range(_SEARCH_RETRIES + 1):
                await gate.wait()
                try:
                    payload = await registry.execute_tool(
                        backend, {"query": query, "count": per_query}
                    )
                except Exception as exc:
                    last_err = str(exc)
                    logger.warning(f"{backend} error for {query!r} (attempt {attempt + 1}): {exc}")
                    err_str = last_err.lower()
                    if "tool_failed[blocked]" in err_str or "repetition" in err_str:
                        break
                    continue
                payload = payload if isinstance(payload, dict) else {}
                if not payload.get("success"):
                    last_err = str(payload.get("error") or "search returned no results")
                    logger.warning(f"{backend} miss for {query!r} (attempt {attempt + 1}): {last_err}")
                    err_str = last_err.lower()
                    if (
                        "no organic results" in err_str
                        or "tool_failed[blocked]" in err_str
                        or "repetition" in err_str
                    ):
                        break
                    continue
                parsed = _parse_search_results(payload)[:per_query]
                # Only scraped backends exhibit the "success but garbage" soft-degradation
                # (issue #523); trusted paid APIs (Brave, Serper) are accepted as-is.
                if parsed and backend in _SCRAPED_BACKENDS and not _looks_relevant(query, parsed):
                    # Backend answered but the results share no meaningful term with the
                    # query (e.g. a scraped engine returning generic keyword-soup) — keep
                    # them as a last-resort fallback but keep trying the chain for real hits.
                    last_err = "results returned but none matched the query terms"
                    logger.warning(f"{backend} low-relevance results for {query!r} — trying next backend")
                    if not weak_hits:
                        weak_hits, weak_backend = parsed, backend
                    continue
                if parsed:
                    hits = parsed
                    search_backend = backend
                    break
            if hits:
                break
            if backend == "searxng_search" and len(backends) > 1:
                searxng_failed = True
                logger.error(
                    f"[ SEARCH ] SearXNG JSON empty for {query!r}: {last_err} "
                    f"— trying Playwright/Brave fallback"
                )
            elif backend == "playwright_search" and len(backends) > 1:
                logger.warning(
                    f"[ SEARCH ] Playwright search miss for {query!r}: {last_err} "
                    f"— trying Brave fallback"
                )
            elif len(backends) > 1:
                logger.warning(f"[ SEARCH ] Falling back from {backend} for {query!r}: {last_err}")

        if not hits and weak_hits:
            # No backend in the chain produced a relevant hit — use the best low-relevance
            # result as a last resort rather than reporting total failure; better to hand
            # the agent something (flagged) than nothing.
            logger.warning(f"[ SEARCH ] Using low-relevance {weak_backend} results for {query!r} (no backend matched)")
            hits, search_backend = weak_hits, weak_backend

        if not hits:
            unavailable = (
                "SearXNG JSON returned no results and all fallbacks failed. "
                "Do NOT invent URLs or web facts."
                if searxng_failed
                else last_err or "No readable results found."
            )
            return {
                "query": query,
                "sources": [],
                "source_count": 0,
                "message": unavailable,
                "searxng_failed": searxng_failed,
                "search_backend": None,
                "search_available": False,
            }

        if not fetch_content:
            return {
                "query": query,
                "sources": [
                    {"url": h["url"], "title": h["title"], "snippet": h.get("description", "")}
                    for h in hits
                ],
                "source_count": len(hits),
                "search_backend": search_backend,
                "searxng_failed": searxng_failed,
            }

        # Stagger same-domain hits slightly to be polite.
        domain_counts: Dict[str, int] = {}
        tasks = []
        for h in hits:
            domain = urlparse(h["url"]).netloc.lower()
            delay = domain_counts.get(domain, 0) * 0.8
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            tasks.append(self._fetch_and_extract(registry, h, delay))

        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        sources = [f for f in fetched if isinstance(f, dict)]
        ok = sum(1 for s in sources if s.get("success"))
        return {
            "query": query,
            "sources": sources,
            "source_count": len(sources),
            "successful_fetches": ok,
            "search_backend": search_backend,
            "searxng_failed": searxng_failed,
        }

    async def _fetch_and_extract(
        self, registry: Any, hit: Dict[str, str], delay: float
    ) -> Dict[str, Any]:
        url = hit["url"]
        entry: Dict[str, Any] = {
            "url": url,
            "title": hit.get("title", "Untitled"),
            "content": "",
            "content_source": None,
            "success": False,
            "error": None,
        }
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            resolved = await resolve_url_content(
                registry, hit, fetch_timeout=_FETCH_TIMEOUT_S, max_chars=_MAX_CONTENT_CHARS
            )
            entry["content"] = resolved.get("content") or ""
            entry["content_source"] = resolved.get("content_source")
            entry["success"] = bool(resolved.get("success"))
            entry["error"] = resolved.get("error")
            if resolved.get("media"):
                entry["media"] = resolved["media"]
        except Exception as exc:
            logger.error(f"fetch/extract error for {url}: {exc}")
            entry["error"] = str(exc)
        return entry
