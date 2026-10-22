# Visual output — what Chad can build for you

Chad is expanding beyond chat text into **downloadable visual artifacts**: diagrams, PDFs, slide decks, HTML previews, and (soon) video. This guide is the map — for users and for agents wiring new capabilities.

## Quick picker

| You want… | Chad path | Settings / service |
|-----------|-----------|-------------------|
| Flowchart, architecture, sequence diagram | `load_skill('kroki')` → `render_diagram` | Kroki (`visual.kroki_url`, optional Docker) |
| **AI image / illustration / concept** | `load_skill('image_generation')` → `generate_image` | **ComfyUI** (`visual.comfyui_url`) |
| Designed PDF / report | `load_skill('typst')` → `compile_typst` | Typst CLI (`visual.typst_bin`) |
| Quick slides from markdown | `load_skill('marp')` → `render_marp` | Marp via `npx` (`visual.marp_command`) |
| **AI-branded presentation** | `load_skill('presenton')` → `generate_presentation` | **Settings → Services → Presenton** |
| Edit an existing `.pptx` | `load_skill('pptx')` | — |
| Animated SVG / filmstrip | `load_skill('animation_preview')` → `render_animation` | — |
| Interactive HTML page | `delegate_to_code_agent` (owns `build_html_file`; optional `load_skill('web-artifacts-builder')`) | — |
| HTML page **with photos/art** | `delegate_to_code_agent` (`search_images` / `download_file` / `build_html_file` inside) | Openverse (no key); optional Pexels/Unsplash/Pixabay |
| **Places on a map** (nearby POIs, directions) | `load_skill('osm-maps')` → `geocode` / `search_places` / `get_route` → `show_map` | Free OpenStreetMap — no API key |
| **Social post / flyer / poster (Canva)** | `load_skill('canva')` → `call_mcp_tool(server_name='canva', ...)` | **Settings → Connectors → Canva** (browser OAuth) |
| **Video** (roadmap) | *not yet* | — |

Deep dives: [PRESENTON.md](PRESENTON.md) · Kroki skill · Typst skill · Marp skill · Canva skill (`src/skills/canva/SKILL.md`)

### Images in local HTML

Chad's websites are usually **local unpublished `.html` files**. For those, `search_images` finds Openverse (Creative Commons) results with no API key, plus optional Pexels/Unsplash/Pixabay keys (**Settings → Connectors → Stock Images**), then falls back to the **same Default Search backend** as `web_search` (Settings → Services: SearXNG / Brave / Serper image mode). Openverse health appears under **Settings → Services → Infrastructure Health**. Embed result URLs in `<img src>`, or `download_file` into `assets/` for offline use. Prefer style/aesthetic queries (e.g. "anime aesthetic neon city") over studio character names.

## How Chad knows to offer visual work

Three layers work together:

### 1. Conversation (proactive offers)

The supervisor prompt includes **visual routing** (`src/infrastructure/tools/visual_routing.py`). When your request clearly fits a deck, diagram, or PDF, Chad may ask once:

- *"Want me to turn this into a slide deck?"*
- *"I can render that as a diagram — prefer SVG or PNG?"*
- *"Want a designed PDF?"*

You can always say yes, no, or pick a format.

### 2. Skills catalog (discovery)

Every visual workflow has a **skill** (`src/skills/<name>/SKILL.md`) listed in:

- **Settings → Skills** (read-only catalog)
- Supervisor turn context (`list_skills` / catalog injection)

Skills teach Chad *how* to use the tools — not executable by themselves.

### 3. Tool selection (execution)

When you ask directly ("make me a deck on X"), hybrid tool selection binds the right native tool (`render_diagram`, `generate_presentation`, etc.). Registry **bundles** auto-inject the matching skill:

- `visual_diagram` → kroki  
- `visual_typst` → typst  
- `visual_marp` → marp  
- `visual_presenton` → presenton  
- `visual_animation` → animation_preview  
- `places_maps` → osm-maps  

## Presenton (AI presentations)

Self-hosted [Presenton](https://presenton.ai/) (Apache 2.0). See [PRESENTON.md](PRESENTON.md).

**Enable:** Settings → Services → Presenton → toggle on, pick LM Studio model, save (restarts Docker sidecar).

**Agent workflow:**

1. Confirm topic + slide count (default ~8).
2. `load_skill('presenton')`
3. `generate_presentation(content=..., n_slides=8, template="general", export_as="pptx")`
4. Share download artifact + optional `edit_url` for Presenton's web editor.

Presenton also ships an [MCP server](https://docs.presenton.ai/v3/guide/generate-presentation-over-mcp) (`/mcp`); Chad uses the native REST tool instead for tighter artifact delivery.

## Canva (graphic design)

Editable social posts, flyers, posters, and marketing graphics via Canva's remote MCP.

**Enable:** Settings → Connectors → Canva → Connect (browser OAuth through `mcp-remote`). Same wizard pattern as Supabase.

**Agent workflow:**

1. Confirm format (Instagram post, story, LinkedIn, poster size, etc.).
2. `load_skill('canva')`
3. `call_mcp_tool(server_name="canva", tool_name="generate-design", arguments={...})`
4. Share Canva edit link; export with `tool_name="export-design"` when the user wants a PNG/PDF file.

Free Canva works for generate/edit/export with a **monthly AI allowance**. Resize and premium exports need Pro+. Autofill/brand templates need Enterprise. Do not confuse with **Canvas LMS**.

Skill: [`src/skills/canva/SKILL.md`](../../src/skills/canva/SKILL.md) · Setup: [CONNECTORS.md](../setup/CONNECTORS.md)

## AI Image Generation (ComfyUI / Local Diffusion)

High-quality local image synthesis using local GPU checkpoints via ComfyUI.

- **Endpoint:** `visual.comfyui_url: http://127.0.0.1:8188`
- **Skill:** `load_skill('image_generation')`
- **Tool:** `generate_image(prompt=..., negative_prompt=..., aspect_ratio="landscape"|"portrait"|"square", steps=20, cfg=7.0)`
- **Display & Actions:** Generated images automatically render directly **inline in the chat message stream** with instant **"Copy Image"** (clipboard blob) and **"Save"** buttons. Click the image to view high-resolution zoom in lightbox. Auto-download is suppressed to keep browser downloads tidy.
- **Concurrency & VRAM Protection:** Coordinated through `TurnConcurrencyGate` to ensure background webhooks/cron never compete with active local generation.

Skill: [`src/skills/image_generation/SKILL.md`](../../src/skills/image_generation/SKILL.md)

## Diagrams (Kroki)


Text-based diagram languages → SVG, PNG, or PDF.

- **Public default:** `https://kroki.io` (zero setup)
- **Self-host:** `docker/chad-services.yml` → `visual.kroki_url: http://127.0.0.1:8000`

Supported engines include Mermaid, PlantUML, D2, GraphViz, ERD. Always `load_skill('kroki')` before `render_diagram`.

## PDFs (Typst)

Typst markup → polished PDF. Good for reports, one-pagers, math-heavy docs. Requires Typst CLI (`winget install Typst.Typst`).

## Slides (Marp)

Markdown with `---` slide breaks → PDF, PPTX, or HTML. Fast and local — no Docker. Use when the user wants **simple** slides from bullets, not AI layout.

**vs Presenton:** Marp = you write markdown; Presenton = AI designs slides from a brief.

## Health & configuration

- **Settings → Services** — infrastructure health cards (Core / Enhanced / Optional)
- `GET /api/services/health` or `.\scripts\setup\verify_chad_services.ps1`
- Service tiers: [SERVICES_AND_MCPS.md](../guides/SERVICES_AND_MCPS.md)

## Roadmap: video

Video export is **not implemented yet**. Do not promise MP4/screen recordings until a `visual_video` bundle lands. Track via GitHub issues when scoped.

## For developers adding a visual capability

1. Native tool under `src/tools/visual/` with `format_action` + `tool-ui.tsx` entry  
   — **or** MCP connector (see Canva / Excalidraw): catalog entry in `ConnectorsTab.tsx`, skill, allowlist, visual routing  
2. `SKILL.md` under `src/skills/<name>/` with routing table  
3. Add to `skill_allowlist.py`  
4. Registry bundle in `src/infrastructure/tools/registry.py` (native tools)  
5. Row in `visual_routing.py` + this doc  
6. Optional: Settings UI if it needs a sidecar (Presenton) or Connectors OAuth tile (Canva / Supabase)
