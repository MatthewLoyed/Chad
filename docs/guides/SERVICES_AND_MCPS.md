# Services vs. MCP Add-ons (issue #363)

Chad has two different kinds of optional dependency. Keep them mentally (and in the UI,
eventually — see #371) separate:

## Services — infrastructure Chad calls directly

Examples: Brain (`:8767`), LM Studio (`:1234`), SearXNG (`:8080`), Kroki, Typst/Marp CLIs.

| Tier | Meaning | Examples |
|---|---|---|
| **Core** | Chad is broken or severely degraded without it | Brain, LM Studio (local-model mode only) |
| **Enhanced** | Core features still work via a fallback or degraded mode | SearXNG → Brave; self-hosted Kroki → public `kroki.io` |
| **Optional** | Nice-to-have, no fallback needed because nothing depends on it by default | self-hosted Kroki for privacy, Presenton, RenderLens |

Check health at `GET /api/services/health` (`src/remote/endpoints.py`) or run
`.\scripts\setup\verify_chad_services.ps1`. Start the optional self-hosted sidecars with:

```powershell
docker compose -f docker/chad-services.yml up -d
```

## MCP Add-ons — optional tool servers discovered at boot

Examples: RenderLens (animated SVG QA), excalidraw (live whiteboard canvas, `mcp_servers.excalidraw`
in `config/config.yaml`, canvas sidecar via `docker compose -f docker/chad-services.yml --profile
excalidraw up -d`), Canva (graphic design via `mcp-remote` → `https://mcp.canva.com/mcp`, enable in
**Settings → Connectors → Canva**). Configured under `mcp_servers` in `config/config.yaml` (or added
by the Connectors wizard), discovered by `MCPScanner` at startup. Never required for basic Chad
operation — they add specific tools, not infrastructure.

**Every entry under `mcp_servers` must have a matching `src/skills/<name>/SKILL.md`** (see
`src/skills/excalidraw/SKILL.md` for the pattern) — we didn't write these third-party servers
ourselves, so the skill is what teaches the model (and the reader) the tool names, workflow order,
and gotchas. `skill_allowlist.py` must list the skill name or `load_skill()` will refuse it.

## Why the distinction matters

A Service being down degrades a *tier* of behavior across many tools (e.g. all web search).
An MCP being disconnected only removes the specific tools it powers. Error messages and UI
should say which one is at fault so the user knows whether to check Docker/a local process
(Service) or an MCP server config (MCP) — see #371 for the in-GUI version of this.

**Visual outputs** (diagrams, PDFs, slides, Presenton decks): see `docs/features/VISUAL_OUTPUT.md`.
