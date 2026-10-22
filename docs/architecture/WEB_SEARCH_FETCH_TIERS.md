# Web Search — Fetch Tier Escalation (Design)

How `web_search` turns URLs into LLM-friendly text **without involving the model**
in fetch or escalation decisions.

Related: [`docs/features/WEB_SEARCH.md`](../features/WEB_SEARCH.md) (routing, config),
[`docs/features/WEB_SEARCH_MEDIA.md`](../features/WEB_SEARCH_MEDIA.md) (future images/video),
issue [#257](https://github.com/MatthewLoyed/AIAssistant/issues/257) (stealth Tier-4),
issue [#331](https://github.com/MatthewLoyed/AIAssistant/issues/331) (rich media).

---

## Design principles

1. **One agent-facing tool** — the model calls `web_search(queries=[...])` only. It never
   chooses `fetch_html`, Playwright MCP, or extraction strategy.
2. **Code-owned escalation** — tiers run inside `fetch_pipeline.resolve_url_content()`.
   Thresholds are constants + config flags, not prompt instructions.
3. **Separate from browser_agent** — Tier-2 uses embedded headless Chromium
   (`playwright.async_api`), not the Playwright MCP server or ChadBrowser profile.
4. **Fail soft, not garbage** — regex-salvage CSS soup is removed. Weak page extracts lose
   to search snippets; bot walls eventually need Tier-4 stealth (#257).
5. **Observable output** — every source includes `content_source` (e.g.
   `fetch_html:trafilatura_precision`, `playwright:trafilatura_precision`, `search_snippet`)
   so UI and logs show which tier won.

---

## End-to-end flow

```
Model: web_search(queries=[...])
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  SEARCH (per query, code)                                   │
│  searxng_search → playwright_search → Brave fallback        │
│  Returns: [{url, title, description}, ...]                  │
└─────────────────────────────────────────────────────────────┘
         │
         ▼  for each hit (parallel, fetch_content=true)
┌─────────────────────────────────────────────────────────────┐
│  fetch_pipeline.resolve_url_content(registry, hit)          │
│                                                             │
│  ┌─ Domain cache hit? ──► Tier 2 Playwright first          │
│  │                                                          │
│  ├─ Tier 1: registry.execute_tool("fetch_html")             │
│  │     └─ content_extraction.extract_from_html()            │
│  │        (trafilatura markdown → readability → quality gate)│
│  │     Score ≥ 25? ──► return                               │
│  │                                                          │
│  ├─ Tier 2: fetch_rendered_html()  [headless Chromium]      │
│  │     └─ same extraction + quality gate                    │
│  │     Score ≥ 25? ──► return, cache domain                 │
│  │                                                          │
│  └─ Tier 3: hit["description"] (SearXNG snippet)            │
│        quality gate ──► return or fail                      │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
Tool result: { content, content_source, success, error? }
```

### What the model does *not* see

| Internal step | Agent bindable? |
|---------------|-----------------|
| `searxng_search` / `brave_web_search` | No — `web_search` dispatches |
| `fetch_html` (MCP) | No — `SUPERVISOR_DELEGATE_ONLY_TOOLS` |
| Playwright MCP (`browser_*`) | No — `browser_agent` only |
| Tier-2 headless Playwright | No — Python-only in `playwright_fetch.py` |

---

## Tier details

### Tier 1 — `fetch_html` + markdown extract

- **Fetch:** MCP `fetch` server via `tool_registry.execute_tool("fetch_html", …)`.
- **Extract:** `content_extraction.extract_from_html()` — trafilatura (markdown, precision
  then recall) → readability-lxml + markdownify → **quality gate**.
- **Accept when:** `llm_friendliness_score ≥ 25` (prose-rich, no CSS/bot-wall patterns).
- **Typical wins:** vendor sites, docs (MDN, Python, Stripe), government pages, PyPI.

### Tier 2 — Headless Playwright render

- **When:** Tier-1 fetch failed, extract rejected, or score `< 25`.
- **How:** Shared Chromium in `playwright_fetch.py` (`tools.web_search_playwright_fallback`,
  default `true`). **Not** the browser_agent MCP stack.
- **Domain cache:** Successful Tier-2 domains skip Tier-1 on repeat hits (in-process set).
- **Typical wins:** Reddit, Stack Overflow, Instagram, Wikipedia (when plain fetch 403),
  IMDb, Britannica, Facebook (partial).

### Tier 3 — Search snippet

- **When:** Tiers 1–2 cannot beat the SearXNG `description` / quality gate.
- **Source:** Already returned by `searxng_search` on the search step — no extra fetch.
- **Typical wins:** WeddingWire, The Knot, Etsy, Yelp, thin JS shells (mcordovamedia).

### Tier 4 — Stealth fetch (planned, #257)

- Firecrawl / residential proxy for hard bot walls (Amazon, Home Depot PerimeterX).
- Only on Tier-1+2+3 failure; per-domain cache to control cost.

---

## Quality gate (why CSS soup is gone)

`passes_quality_gate()` rejects:

- CSS noise (`rgba(`, `.class {`, high `{`/`}` density)
- Bot walls (`Access Denied`, `errors.edgesuite.net`)
- SPA shells (`Loading Loading Loading…`)
- HTML entity soup, Facebook error pages, Wikipedia robot-policy stubs

`select_page_or_snippet()` prefers a richer search snippet over a thin nav extract.

---

## Measured results (2026-07-04, 52-site catalog)

Benchmark: `scripts/diagnostics/benchmark_web_extraction.py --playwright`  
Catalog: `scripts/diagnostics/web_extraction_catalog.json` (19 categories)

| Metric | Tier-1 bench (httpx+extract) | Full prod pipeline |
|--------|-------------------------------|---------------------|
| Page extract OK | 35/52 (67%) | 36/52 (69%) |
| **Final usable** | **52/52 (100%)** | **52/52 (100%)** |
| Total quality score | 11,836 | **18,989 (+60%)** |

### Winning tier (full pipeline)

| Tier | URLs |
|------|------|
| Tier 1 `fetch_html` | 27 |
| Tier 2 Playwright | 16 |
| Tier 3 snippet | 9 |

### Largest Playwright gains (score delta vs tier-1 bench)

| Site | Before → After |
|------|----------------|
| IMDb | 12 → 999 |
| Stack Overflow | 11 → 992 |
| Instagram | 10 → 985 |
| Reddit r/Python | 9 → 765 |
| Britannica | 15 → 705 |
| Wikipedia (wedding) | 12 → 670 |

### Still snippet-only (Tier-4 candidates)

`mcordovamedia`, `weddingwire`, `theknot`, `homedepot_category`, `reuters_business`,
`wirecutter_drill`, `react_quickstart`, `yelp_restaurant`, `etsy_search`

---

## CI vs manual benchmark

| Check | Where | Network | Blocks merge? |
|-------|-------|---------|---------------|
| `tests/unit/test_content_extraction.py` | GitHub Actions (`evals.yml`) | No | Yes |
| `tests/unit/test_fetch_pipeline.py` | GitHub Actions (`evals.yml`) | No | Yes |
| 52-site live benchmark | Local / manual dispatch | Yes | No (too slow/flaky for PR CI) |

```powershell
# Manual full pipeline (matches prod)
$env:PYTHONPATH="."; python scripts/diagnostics/benchmark_web_extraction.py --playwright
$env:PYTHONPATH="."; python scripts/diagnostics/compare_extraction_runs.py
```

---

## Prod deployment checklist

After merge to `master`:

1. `pip install -r requirements.txt` (adds `readability-lxml`, `markdownify`)
2. `playwright install chromium` in the Brain venv
3. Restart Brain (supervisor / Settings → Update)
4. Confirm `tools.web_search_playwright_fallback: true` in config
5. Optional smoke: one `web_search` call; verify `content_source` in tool result (no CSS)

---

## Key modules

| Module | Responsibility |
|--------|----------------|
| `src/tools/web/web_search.py` | Agent API, search orchestration, parallel fetches |
| `src/tools/web/fetch_pipeline.py` | Tier escalation orchestration |
| `src/tools/web/content_extraction.py` | Markdown extract + quality gate + snippet pick |
| `src/tools/web/playwright_fetch.py` | Tier-2 headless browser pool |
| `src/infrastructure/tools/delegate_routing.py` | Keeps internal tools off agent menu |
