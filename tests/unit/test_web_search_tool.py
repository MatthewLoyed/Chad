"""WebSearchTool — SearXNG-primary/Brave-fallback backend chain and multi-query batching.

Highest-yield gap identified for issue #216: src/tools/web/web_search.py had zero test
coverage before this file, despite being the single entry point for every web-search-driven
flow (supervisor quick lookups + research_agent deep research). Tests here isolate the
search/fallback/aggregation logic from the fetch pipeline (already covered by
test_fetch_pipeline.py) by patching resolve_url_content directly.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.web.web_search import WebSearchTool, _parse_search_results, _looks_relevant, _backend_chain

BRAVE_JSON_PAYLOAD = {
    "success": True,
    "result": [{"text": (
        '{"web": {"results": ['
        '{"url": "https://example.com/a", "title": "A", "description": "about a"},'
        '{"url": "https://example.com/b", "title": "B", "description": "about b"}'
        "]}}"
    )}],
}

SEARXNG_PLAIN_TEXT_PAYLOAD = {
    "success": True,
    "result": (
        "Title: SearXNG Result One\nURL: https://searx-example.com/one\n"
        "Description: first hit\n\n"
        "Title: SearXNG Result Two\nURL: https://searx-example.com/two\n"
        "Description: second hit"
    ),
}


def _config_get(overrides):
    defaults = {
        "tools.search_backend": "searxng",
        "tools.web_search_max_queries": 10,
        "tools.web_search_results_per_query": 3,
        "tools.web_search_playwright_search_fallback": False,
    }
    defaults.update(overrides)
    return lambda key, default=None: defaults.get(key, default)


class _FakeRegistry:
    """Routes execute_tool by backend name to a scripted response queue per backend."""

    def __init__(self, responses: dict[str, list]):
        # responses: {"searxng_search": [payload_or_exception, ...], "brave_web_search": [...]}
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    async def execute_tool(self, name, args):
        self.calls.append((name, args))
        queue = self._responses.get(name, [])
        if not queue:
            return {"success": False, "error": f"no mock scripted for {name}"}
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _default_config(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(_config_get({})))
    # Brave "configured" by default (matches pre-#523 behavior: Brave was always the
    # unconditional last-resort fallback). Serper/Tavily stay unconfigured by default —
    # they're the new opt-in backends; individual tests override this to exercise them.
    monkeypatch.setattr(
        ConfigManager, "get_secret",
        staticmethod(lambda key, default=None: "fake-brave-key" if key == "BRAVE_API_KEY" else default),
    )


def _patched_resolve(content="extracted article text", success=True):
    async def _fake(registry, hit, fetch_timeout=30.0, max_chars=6000):
        return {
            "content": content,
            "content_source": "fetch_html:trafilatura_precision",
            "success": success,
            "error": None if success else "fetch failed",
        }
    return _fake


# --- _parse_search_results (pure function) --------------------------------- #

def test_parse_search_results_json_block():
    items = _parse_search_results(BRAVE_JSON_PAYLOAD)
    assert [i["url"] for i in items] == ["https://example.com/a", "https://example.com/b"]


def test_parse_search_results_plain_text_blocks():
    items = _parse_search_results(SEARXNG_PLAIN_TEXT_PAYLOAD)
    assert [i["url"] for i in items] == [
        "https://searx-example.com/one", "https://searx-example.com/two",
    ]
    assert items[0]["title"] == "SearXNG Result One"


def test_parse_search_results_drops_engine_domains_and_dedupes():
    payload = {
        "success": True,
        "result": (
            "[Real Result](https://example.com/real)\n"
            "[Google Clutter](https://google.com/search?q=x)\n"
            "[Dup](https://example.com/real)"
        ),
    }
    items = _parse_search_results(payload)
    assert [i["url"] for i in items] == ["https://example.com/real"]


def test_parse_search_results_failure_payload_returns_empty():
    assert _parse_search_results({"success": False, "error": "boom"}) == []


# --- backend chain: SearXNG primary, Brave fallback ------------------------ #

@pytest.mark.asyncio
async def test_searxng_success_never_calls_brave():
    reg = _FakeRegistry({"searxng_search": [SEARXNG_PLAIN_TEXT_PAYLOAD]})
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["chad searxng result"], tool_registry=reg)

    assert result["success"]
    assert result["searxng_failures"] is None
    assert result["brave_fallbacks"] is None
    called_backends = {name for name, _ in reg.calls}
    assert called_backends == {"searxng_search"}
    assert result["results"][0]["search_backend"] == "searxng_search"


@pytest.mark.asyncio
async def test_searxng_failure_falls_back_to_brave_with_warning():
    reg = _FakeRegistry({
        "searxng_search": [{"success": False, "error": "connection refused"}],
        "brave_web_search": [BRAVE_JSON_PAYLOAD],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["nvidia stock price"], tool_registry=reg)

    assert result["success"]
    assert result["searxng_failures"] == 1
    assert result["brave_fallbacks"] == 1
    assert result["search_status"] == "degraded"
    assert result["searxng_connected"] is False
    assert "JSON returned no results" in result["caption"]
    r = result["results"][0]
    assert r["searxng_failed"] is True
    assert r["search_backend"] == "brave_web_search"


@pytest.mark.asyncio
async def test_searxng_exception_also_triggers_fallback():
    """A raised exception (network error) must be treated the same as a success:false
    payload — both are recoverable via the Brave fallback, not a crashed turn."""
    reg = _FakeRegistry({
        "searxng_search": [ConnectionError("searxng unreachable")],
        "brave_web_search": [BRAVE_JSON_PAYLOAD],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["anything"], tool_registry=reg)

    assert result["success"]
    assert result["results"][0]["search_backend"] == "brave_web_search"


@pytest.mark.asyncio
async def test_retries_same_backend_before_falling_back():
    """One transient miss on SearXNG should retry SearXNG itself (not immediately jump to
    Brave) — only after all retries on a backend are exhausted does it move to the next."""
    reg = _FakeRegistry({
        "searxng_search": [
            {"success": False, "error": "transient"},
            SEARXNG_PLAIN_TEXT_PAYLOAD,
        ],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["retry me searxng result"], tool_registry=reg)

    searxng_calls = [c for c in reg.calls if c[0] == "searxng_search"]
    assert len(searxng_calls) == 2, "must retry the SAME backend before falling back"
    assert result["results"][0]["search_backend"] == "searxng_search"
    assert result["searxng_failures"] is None


@pytest.mark.asyncio
async def test_searxng_failure_uses_playwright_before_brave(monkeypatch):
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(_config_get({"tools.web_search_playwright_search_fallback": True})),
    )
    reg = _FakeRegistry({
        "searxng_search": [{"success": False, "error": "SearXNG returned no results."}],
        "playwright_search": [SEARXNG_PLAIN_TEXT_PAYLOAD],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["nvidia stock searxng result"], tool_registry=reg)

    assert result["success"]
    assert result["searxng_failures"] == 1
    assert result["playwright_fallbacks"] == 1
    assert result["brave_fallbacks"] is None
    assert result["results"][0]["search_backend"] == "playwright_search"
    backends_called = [name for name, _ in reg.calls]
    assert "playwright_search" in backends_called
    assert "brave_web_search" not in backends_called


@pytest.mark.asyncio
async def test_all_backends_exhausted_returns_no_content_not_a_crash():
    reg = _FakeRegistry({
        "searxng_search": [{"success": False, "error": "down"}] * 3,
        "brave_web_search": [{"success": False, "error": "also down"}] * 3,
    })
    tool = WebSearchTool()
    result = await tool.execute(queries=["nothing will be found"], tool_registry=reg)

    assert result["success"] is False
    assert result["search_status"] == "unavailable"
    assert result["searxng_connected"] is False
    assert "JSON returned no results" in result["error"]
    r = result["results"][0]
    assert r["source_count"] == 0
    assert r["searxng_failed"] is True
    assert r["search_available"] is False


# --- multi-query batching: independent per-query backend outcome ---------- #

@pytest.mark.asyncio
async def test_multi_query_batch_aggregates_per_query_fallback_independently():
    reg = _FakeRegistry({
        "searxng_search": [
            SEARXNG_PLAIN_TEXT_PAYLOAD,  # query 1: searxng hits
            {"success": False, "error": "down"},  # query 2: searxng misses -> brave
        ],
        "brave_web_search": [BRAVE_JSON_PAYLOAD],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(
            queries=["query one searxng result", "query two"], tool_registry=reg
        )

    assert result["query_count"] == 2
    assert result["searxng_failures"] == 1
    assert result["brave_fallbacks"] == 1
    backends_used = {r["query"]: r["search_backend"] for r in result["results"]}
    assert backends_used["query one searxng result"] == "searxng_search"
    assert backends_used["query two"] == "brave_web_search"


# --- fetch_content=False: links/snippets only, no fetch pipeline call ------ #

@pytest.mark.asyncio
async def test_fetch_content_false_skips_page_fetch_entirely():
    reg = _FakeRegistry({"searxng_search": [SEARXNG_PLAIN_TEXT_PAYLOAD]})
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new_callable=AsyncMock) as resolve_mock:
        result = await tool.execute(
            queries=["quick lookup searxng result"], tool_registry=reg, fetch_content=False
        )

    resolve_mock.assert_not_called()
    r = result["results"][0]
    assert r["source_count"] == 2
    assert all("snippet" in s for s in r["sources"])


# --- validate_params -------------------------------------------------------- #

def test_validate_params_rejects_empty_queries():
    tool = WebSearchTool()
    ok, err = tool.validate_params(queries=[])
    assert not ok and "non-empty" in err


def test_validate_params_rejects_too_many_queries():
    tool = WebSearchTool()
    ok, err = tool.validate_params(queries=["q"] * 11)
    assert not ok and "Maximum" in err


def test_validate_params_rejects_blank_entries():
    tool = WebSearchTool()
    ok, err = tool.validate_params(queries=["good", "   "])
    assert not ok and "queries[1]" in err


# --- _looks_relevant: soft-degraded ("success but garbage") result detection ---- #

def test_looks_relevant_true_when_a_hit_shares_a_query_term():
    hits = [{"title": "Dream Events & Catering", "description": "Nashville catering", "url": "https://x.com"}]
    assert _looks_relevant("Dream Events & Catering Nashville TN", hits) is True


def test_looks_relevant_false_when_no_hit_shares_any_term():
    # Real failure mode observed for issue #523: a scraped-engine soft-degradation
    # returns "success" with plausible-looking but wholly unrelated results.
    hits = [
        {"title": "Dream (YouTuber) - Wikipedia", "description": "Minecraft speedruns", "url": "https://en.wikipedia.org/wiki/Dream_(YouTuber)"},
    ]
    assert _looks_relevant("Dream Events & Catering Nashville TN", hits) is False


def test_looks_relevant_true_for_generic_query_with_no_distinctive_terms():
    hits = [{"title": "anything", "description": "", "url": "https://x.com"}]
    assert _looks_relevant("what is the best for near", hits) is True  # all stopwords -> unjudgeable


# --- backend chain: low-relevance SearXNG result is treated as a miss ---------- #

SEARXNG_IRRELEVANT_PAYLOAD = {
    "success": True,
    "result": (
        "Title: Dream (YouTuber) - Wikipedia\nURL: https://en.wikipedia.org/wiki/Dream_(YouTuber)\n"
        "Description: Minecraft speedruns and manhunts"
    ),
}


@pytest.mark.asyncio
async def test_low_relevance_searxng_hits_fall_through_to_brave():
    reg = _FakeRegistry({
        "searxng_search": [SEARXNG_IRRELEVANT_PAYLOAD] * 3,  # retries exhaust too
        "brave_web_search": [BRAVE_JSON_PAYLOAD],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["Dream Events & Catering Nashville TN"], tool_registry=reg)

    assert result["success"]
    assert result["results"][0]["search_backend"] == "brave_web_search"
    assert result["searxng_failures"] == 1


@pytest.mark.asyncio
async def test_low_relevance_everywhere_still_returns_weak_hits_not_total_failure():
    reg = _FakeRegistry({
        "searxng_search": [SEARXNG_IRRELEVANT_PAYLOAD] * 3,
        "brave_web_search": [{"success": False, "error": "down"}] * 3,
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["Dream Events & Catering Nashville TN"], tool_registry=reg)

    # No backend produced a relevant hit and Brave hard-failed too — better to surface
    # the weak SearXNG result than report total failure, since the heuristic can have
    # false negatives.
    assert result["success"]
    assert result["results"][0]["source_count"] > 0
    assert result["results"][0]["search_backend"] == "searxng_search"


# --- backend chain: Serper only joins when SERPER_API_KEY is configured -------- #

@pytest.mark.asyncio
async def test_serper_excluded_from_chain_by_default(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: default))
    reg = _FakeRegistry({
        "searxng_search": [SEARXNG_IRRELEVANT_PAYLOAD] * 3,
        "brave_web_search": [BRAVE_JSON_PAYLOAD],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        await tool.execute(queries=["anything"], tool_registry=reg)

    called_backends = {name for name, _ in reg.calls}
    assert "serper_search" not in called_backends


@pytest.mark.asyncio
async def test_serper_joins_chain_when_key_configured(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: "fake-key"))
    reg = _FakeRegistry({
        "searxng_search": [SEARXNG_IRRELEVANT_PAYLOAD] * 3,
        "serper_search": [BRAVE_JSON_PAYLOAD],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["Dream Events & Catering Nashville TN"], tool_registry=reg)

    assert result["results"][0]["search_backend"] == "serper_search"
    called_backends = {name for name, _ in reg.calls}
    assert "brave_web_search" not in called_backends  # serper hit first, brave never needed


# --- backend chain: search_backend "serper" / "all" (round-robin) -------------- #

def test_backend_chain_serper_mode_is_serper_only(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: (
        "serper" if key == "tools.search_backend" else default
    )))
    chain, _ = _backend_chain()
    assert chain == ["serper_search"]


def test_backend_chain_all_is_api_keys_only_no_searxng(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: (
        "all" if key == "tools.search_backend" else
        False if key == "tools.web_search_playwright_search_fallback" else default
    )))
    # Only Brave has a key; Serper/Tavily don't -> both skipped, not tried-and-failed.
    # "all" must never include searxng_search/playwright_search — API keys only,
    # unless the user explicitly selects search_backend: searxng.
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: (
        "brave-key" if key == "BRAVE_API_KEY" else default
    )))
    chain, _ = _backend_chain()
    assert chain == ["brave_web_search"]
    assert "searxng_search" not in chain


def test_backend_chain_all_returns_empty_when_no_keys_configured(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: (
        "all" if key == "tools.search_backend" else default
    )))
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: default))
    chain, _ = _backend_chain()
    assert chain == []


def test_backend_chain_all_rotates_paid_backend_order(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: (
        "all" if key == "tools.search_backend" else
        False if key == "tools.web_search_playwright_search_fallback" else default
    )))
    # All three configured -> chain should spread usage by alternating which paid
    # backend comes first, rather than always trying the same one before the others.
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: "a-key"))

    chain_a, _ = _backend_chain()
    chain_b, _ = _backend_chain()
    assert set(chain_a) == set(chain_b) == {"serper_search", "tavily_search", "brave_web_search"}
    assert "searxng_search" not in chain_a
    assert chain_a != chain_b, "consecutive calls must rotate the paid-backend order"


@pytest.mark.asyncio
async def test_no_organic_results_skips_retrying_same_backend():
    """When a search engine returns 'no organic results', retrying the exact same
    query is pointless and trips RepetitionGuard. It should immediately fall back."""
    reg = _FakeRegistry({
        "serper_search": [{"success": False, "error": "Serper returned no organic results."}],
        "brave_web_search": [SEARXNG_PLAIN_TEXT_PAYLOAD],
    })
    tool = WebSearchTool()
    with patch("src.tools.web.web_search._backend_chain", return_value=(["serper_search", "brave_web_search"], 0.0)), \
         patch("src.tools.web.web_search.resolve_url_content", new=_patched_resolve()):
        result = await tool.execute(queries=["rare course query"], tool_registry=reg)

    assert result["success"]
    # serper_search should only have been called ONCE, not 3 times
    serper_calls = [c for c in reg.calls if c[0] == "serper_search"]
    assert len(serper_calls) == 1
    assert result["results"][0]["search_backend"] == "brave_web_search"

