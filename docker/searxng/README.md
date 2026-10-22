# Self-hosted SearXNG — Chad's web search backend

> **Canonical doc:** [`docs/features/WEB_SEARCH.md`](../../docs/features/WEB_SEARCH.md)
>
> This file also works standalone, but `docker/chad-services.yml` starts SearXNG
> alongside the other optional sidecars (e.g. Kroki) in one command — prefer that
> unless you specifically want SearXNG on its own. Verify any of them with
> `.\scripts\setup\verify_chad_services.ps1`.

SearXNG is a privacy-respecting **metasearch** engine: it aggregates results from
Google, Bing, DuckDuckGo, Brave, Mojeek, Startpage, Wikipedia, etc. and returns a
single merged JSON list. Self-hosting it gives Chad **unlimited, free, no-API-key**
web search — which is what lets `web_search` run batches of queries per turn
(`tools.web_search_max_queries`, default 10) without hitting Brave's quota.

## Start it

Docker Desktop must be running.

```powershell
docker compose -f docker/searxng/docker-compose.yml up -d
```

`restart: unless-stopped` keeps both containers (SearXNG + Valkey cache) alive across
reboots, matching the always-on prod Brain.

## Verify

```powershell
curl "http://127.0.0.1:8080/search?q=hello+world&format=json"
```

You should get JSON with a `results` array. The browser UI is at
<http://127.0.0.1:8080> for manual debugging.

## How Chad uses it

`config/config.yaml` → `tools.search_backend: searxng`, `tools.searxng_url`,
`tools.web_search_max_queries`, `tools.web_search_results_per_query`.

The `web_search` tool calls the in-process `searxng_search` tool
(`src/tools/web/searxng_search.py`), which hits the JSON endpoint above. If the
container is **unreachable**, Chad logs at ERROR, surfaces warnings in tool output,
and **falls back to Brave** — search still works, but you'll see the failure.

To go back to Brave-only, set `tools.search_backend: brave`.

## Tuning / blocking notes

SearXNG scrapes upstream engines from this machine's IP. Under very heavy sustained
use, Google/Bing may start returning CAPTCHAs. `settings.yml` already mitigates this
by **weighting tolerant engines (DuckDuckGo, Brave, Startpage, Mojeek) above
Google/Bing** so no single upstream absorbs a whole batch, and by enabling the Valkey
cache so repeat queries don't re-hit upstream. If you still see blocking, lower the
Google/Bing weights or `disabled: true` them in `settings.yml` and restart:

```powershell
docker compose -f docker/searxng/docker-compose.yml restart searxng
```

## Stop

```powershell
docker compose -f docker/searxng/docker-compose.yml down
```

## Security

The instance is bound to `127.0.0.1` only and never exposed off-box. If you ever
expose it beyond localhost, change `server.secret_key` in `settings.yml` first.
