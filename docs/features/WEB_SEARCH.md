# Web Search

Chad has one programmatic search tool (`web_search`) and one research orchestrator
(`deep_research_agent`, #510). The model chooses between calling `web_search`
directly vs. delegating based on depth — not based on which backend runs.

## Architecture

```
Supervisor (main model, Gemma-4-26B-A4B)
  ├─ web_search                        → quick lookup (1–2 queries)
  └─ delegate_to_deep_research_agent   → deep / multi-item research
         │
         ▼
     deep_research_agent (SAME model as supervisor — MoE, ~4B active params,
     already resident, no VRAM swap needed — but NEVER calls web_search itself)
         ├─ research_discover (breadth, ONE 9B worker)  — only when the caller
         │     doesn't already know the concrete item names; finds and lists
         │     real items via a couple of broad searches
         ├─ writes one self-contained research brief per item
         ├─ parallel_research (depth, N concurrent 9B workers)  — one worker
         │     per item, each researches its own fields via web_search
         │        └─ research_agent (qwen3.5-9b) × N concurrent
         └─ synthesizes N worker summaries into ONE report, returned to the
               supervisor
```

`research_discover` and `parallel_research` are bound ONLY inside
`deep_research_agent`'s own subgraph tool set — never on the main supervisor —
so the supervisor's own thread stays a single delegate-in/report-out instead of
accumulating dozens of raw `web_search` calls itself (the failure mode this
architecture replaced: a "research 10 caterers" turn used to burn 25 iterations
and 44 raw `web_search` calls directly in the main supervisor's context).

Both `web_search` paths use the same pipeline inside `src/tools/web/web_search.py`.
For the full tier-escalation design, benchmark numbers, and prod checklist see
[`docs/architecture/WEB_SEARCH_FETCH_TIERS.md`](../architecture/WEB_SEARCH_FETCH_TIERS.md).

```
search (SearXNG curated JSON → Playwright Startpage → Brave fallback) → fetch pages → markdown extract + quality gate → snippet fallback
```

Tiered fetch inside each source:

```
fetch_html (Tier 1) → headless Playwright (Tier 2) → SearXNG snippet (Tier 3)
```

`brave_web_search` and `fetch_html` are **internal** MCP steps — agents never
call them directly.

## When to use which

| Situation | Tool |
|-----------|------|
| One fact, price, link, or 1–2 queries | `web_search` on the supervisor |
| Many items, comparisons, buy links for a list, synthesized report — or even one item needing real depth | `delegate_to_deep_research_agent` |
| YouTube inline player | `web_search` with `site:youtube.com` + `fetch_content=false`, then `show_youtube_video` |

## Known gap: `web_search` result quality on long-tail/local queries (open, #510 follow-up)

Live testing (10 real small-business wedding caterers, names pre-supplied) showed
`parallel_research`'s depth workers came back "not found" for 3 businesses that
are genuinely real and independently web-searchable — the search backend failed
to surface them, not a fabrication or over-eager give-up by the model alone
(each worker's `web_search` call budget is currently capped at 3 calls, which may
also be too tight for small businesses that require digging past a homepage).
Not yet root-caused; worth checking SearXNG's raw results for one of the missed
businesses directly before assuming a fix. See issue #510 for the harness/
architecture validation that motivated this finding.

Routing strings live in `src/infrastructure/tools/web_search_routing.py` and are
injected into supervisor prompts, handoff tool descriptions, Scout, and skill catalog text.

## Configuration (`config/config.yaml` → `tools.*`)

| Key | Default | Purpose |
|-----|---------|---------|
| `search_backend` | `searxng` | `searxng` (default chain), `brave`/`serper`/`tavily` (single backend only), or `all` (**API keys only, no SearXNG** — round-robins whichever of Serper/Tavily/Brave are configured) |
| `searxng_url` | `http://127.0.0.1:8080` | Self-hosted SearXNG JSON endpoint |
| `web_search_max_queries` | `10` | Max queries per `web_search` call (batch cap) |
| `web_search_results_per_query` | `3` | Default pages fetched per query (1–5) |
| `web_search_playwright_fallback` | `true` | Tier-2 headless Playwright when Tier-1 extract score is low |
| `web_search_playwright_search_fallback` | `true` | Search-step Playwright Startpage scrape when SearXNG JSON is empty |
| `SERPER_API_KEY` (env, optional) | unset | Opt-in fallback (Serper.dev, ~$0.0003–0.001/query). Off by default — no required keys |
| `TAVILY_API_KEY` (env, optional) | unset | Opt-in fallback (Tavily, 1,000 free/mo). Off by default — no required keys |

SearXNG runs via Docker: see `docker/searxng/README.md`.

### Known gap: self-hosted SearXNG soft-degrades under sustained agent traffic (#523)

SearXNG scrapes upstream engines' public search pages with no proxy/fingerprint
infrastructure. From a single residential/server IP, sustained agent-driven query volume
gets most engines (Google, DuckDuckGo, Brave, Mojeek) rate-limited or CAPTCHA-blocked —
`unresponsive_engines` in the raw JSON response shows this directly. Whichever engine is
still responding (usually Bing) doesn't hard-fail; it **soft-degrades**, returning
`success: true` with real-looking but irrelevant results (e.g. a business-name query
returning dictionary definitions or Wikipedia trivia) instead of an error. That silent
failure mode is why the existing SearXNG → Brave fallback never used to trigger for these
queries — a "success" response with garbage in it looked identical to a good one.

Two changes close this gap:
1. `_looks_relevant()` in `src/tools/web/web_search.py` rejects results that share no
   meaningful term with the query, so a soft-degraded SearXNG response is now treated as a
   miss and the chain keeps going instead of accepting it.
2. `SERPER_API_KEY` / `TAVILY_API_KEY` (both optional) add managed, non-scraped SERP APIs
   as fallback tiers after SearXNG/Playwright. Each only joins the backend chain when its
   key is set; without either, behavior is unchanged. Set `search_backend: all` to skip
   SearXNG entirely and round-robin only across whichever of these (+ Brave) are configured
   — useful once you'd rather spread load across a few free/cheap API quotas than depend on
   scraping at all.

There is no free way to make self-hosted scraping immune to this — see the audit in issue
#523 for why (residential proxy pools + browser fingerprinting are what paid SERP APIs
are actually charging for).

### SearXNG + Brave fallback

With `search_backend: searxng`, SearXNG is tried first. If it fails, Chad logs at
**ERROR**, surfaces warnings in the tool result (`warnings`, `searxng_failures`),
and **falls back to Brave** so search still works when the container is down.

Set `search_backend: brave` for Brave-only (no SearXNG).

## Key files

| File | Role |
|------|------|
| `src/tools/web/web_search.py` | Agent-facing tool + pipeline |
| `src/tools/web/searxng_search.py` | SearXNG HTTP client |
| `src/tools/web/serper_search.py` | Serper.dev HTTP client (optional, opt-in via `SERPER_API_KEY`) |
| `src/tools/web/tavily_search.py` | Tavily HTTP client (optional, opt-in via `TAVILY_API_KEY`) |
| `src/tools/web/content_extraction.py` | Markdown cascade + quality gate |
| `src/tools/web/playwright_fetch.py` | Tier-2 headless Playwright fetch |
| `src/tools/web/fetch_pipeline.py` | Tier orchestration (fetch → extract → snippet) |
| `src/infrastructure/tools/delegate_routing.py` | Supervisor cannot bind internal MCP search tools |
| `src/infrastructure/tools/web_search_routing.py` | Shared when-to-use guidance |
| `src/core/orchestration/agents/factory.py` | `research_agent` (worker) config + fallback prompt |
| `src/core/orchestration/supervisor.py` | Builds `deep_research_agent` (orchestrator) + wires `research_discover`/`parallel_research` as its private tools |
| `src/core/orchestration/handoffs.py` | `create_research_discover_tool` / `create_parallel_research_tool` implementations |
| `docker/searxng/` | Self-hosted metasearch stack |

## Persona / prompt maintenance

Persona fragments are local files — edit `config/persona/<id>.md` (overriding the
`prompts/persona/<id>.md` baseline), or apply changes via
`scripts/update_web_search_persona.py`, which writes through `persona_store` and records
a revertable version. After applying, **restart the Brain** so `PromptRegistry` reloads.
See [PERSONA_PROMPTS.md](../architecture/PERSONA_PROMPTS.md).

Audit: `python -m evals.runner --prompt-audit`

## Diagnostics

```powershell
# SearXNG health
curl "http://127.0.0.1:8080/search?q=test&format=json"

# Full pipeline (SearXNG shim — add SearXNG path separately)
$env:PYTHONPATH="."; python scripts/diagnostics/test_web_search.py

# Live extraction benchmark (50-site catalog, no LLM)
$env:PYTHONPATH="."; python scripts/diagnostics/benchmark_web_extraction.py
$env:PYTHONPATH="."; python scripts/diagnostics/benchmark_web_extraction.py --failures-only
$env:PYTHONPATH="."; python scripts/diagnostics/benchmark_web_extraction.py --playwright
$env:PYTHONPATH="."; python scripts/diagnostics/benchmark_web_extraction.py --json artifacts/web_extraction_baseline.json

# Tier-2 Playwright requires Chromium binaries in the Brain venv:
#   playwright install chromium
```
