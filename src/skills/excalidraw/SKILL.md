---
name: excalidraw
description: Draw hand-drawn-style whiteboard diagrams on a live, persistent Excalidraw canvas via the excalidraw MCP add-on (26 element-level tools — create/update/delete, layout, screenshot, export). Use for informal sketches, brainstorm boards, and diagrams the user wants to keep iterating on live — not for one-shot structured diagrams (kroki), slides (marp/presenton), or PDFs (typst).
license: MIT (skill + yctimlin/mcp_excalidraw upstream)
requires: call_mcp_tool(server_name="excalidraw", ...) — create_element, batch_create_elements, describe_scene, get_canvas_screenshot, export_scene, export_to_image, create_from_mermaid, read_diagram_guide, set_viewport, ...
runtime: node (MCP server) + docker (canvas sidecar)
install: docker compose -f docker/chad-services.yml --profile excalidraw up -d   # then the excalidraw MCP entry in config.yaml auto-connects via MCPScanner
---

# Excalidraw Whiteboard Skill

> Lineage: [yctimlin/mcp_excalidraw](https://github.com/yctimlin/mcp_excalidraw) (MIT, 2k+ stars) — a live, stateful
> canvas + 26 MCP tools, chosen over the smaller one-shot `excalidrawer` alternative (issue #354) because Chad
> benefits from iterative refinement (draw → look → adjust) and a persistent board the user can keep open.

## When to use

| Need | Use instead |
|------|-------------|
| Hand-drawn whiteboard sketch, brainstorm board, informal diagram the user wants to keep open/iterate on | **excalidraw** |
| Structured flowchart / sequence / ER / architecture diagram (one-shot, text-source-driven) | `load_skill('kroki')` → `render_diagram` |
| Slide deck | `load_skill('marp')` or `load_skill('presenton')` |
| Designed PDF/report | `load_skill('typst')` |
| User explicitly says "whiteboard", "sketch", "Excalidraw", or wants a hand-drawn look | **excalidraw** |

## Prerequisites

1. Canvas sidecar running: `docker compose -f docker/chad-services.yml --profile excalidraw up -d` (binds `127.0.0.1:3000`).
2. `excalidraw` MCP server configured under `mcp_servers` in `config/config.yaml` (ships with Chad) — discovered
   at boot by `MCPScanner`. If tools aren't showing up, the canvas sidecar is probably not running; tell the user.

If unavailable, offer `kroki` for a structured diagram instead — don't promise a live canvas you can't reach.

## Agent workflow (mandatory)

1. `load_skill('excalidraw')`
2. Call `read_diagram_guide` once per session for color palette / sizing / anti-pattern rules — apply them to every
   element you place (don't freehand colors or spacing).
3. Prefer **batch_create_elements** over many single `create_element` calls for a diagram with >3 shapes.
4. After drawing, call `describe_scene` (structured text) or `get_canvas_screenshot` (image) to verify layout before
   telling the user it's done — this is a stateful canvas, not a one-shot render; check your work.
5. Use `align_elements` / `distribute_elements` / `group_elements` to tidy layout rather than hand-placing coordinates.
6. Finish with `set_viewport(scrollToContent=true)` so the canvas is framed correctly when the user opens it.
7. Share the live canvas URL (`http://127.0.0.1:3000`) — or `export_to_image` / `export_scene` if the user wants a
   file instead of the live board.

## Tool categories (26 total)

| Category | Tools |
|----------|-------|
| Element CRUD | `create_element`, `get_element`, `update_element`, `delete_element`, `query_elements`, `batch_create_elements`, `duplicate_elements` |
| Layout | `align_elements`, `distribute_elements`, `group_elements`, `ungroup_elements`, `lock_elements`, `unlock_elements` |
| Scene awareness | `describe_scene`, `get_canvas_screenshot` |
| File I/O | `export_scene`, `import_scene`, `export_to_image`, `create_from_mermaid` |
| State | `snapshot_scene`, `restore_snapshot`, `clear_canvas` |
| Viewport | `set_viewport` |
| Sharing / guide | `export_to_excalidraw_url`, `read_diagram_guide` |

## Mermaid shortcut

Already have Mermaid source (or the user asked for a diagram but wants the hand-drawn look instead of Kroki's
clean render)? Use `create_from_mermaid` directly — no need to hand-place elements.

## Do not

- Do not hand-place elements without `read_diagram_guide` first — colors/spacing will look inconsistent.
- Do not use for structured, text-source diagrams the user wants rendered cleanly — that's `kroki`.
- Do not call `export_to_excalidraw_url` (uploads to excalidraw.com) without telling the user their diagram is
  leaving the local machine.
- Do not promise the live canvas works if the sidecar health check fails — degrade to `kroki` instead.

## Reference

- [yctimlin/mcp_excalidraw](https://github.com/yctimlin/mcp_excalidraw) · [official one-shot alternative](https://github.com/excalidraw/excalidraw-mcp)
- Chad: `docs/guides/SERVICES_AND_MCPS.md` · `docs/features/VISUAL_OUTPUT.md`
