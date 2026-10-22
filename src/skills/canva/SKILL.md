---
name: canva
description: Create, edit, review, and export graphic designs via the Canva MCP connector (social posts, posters, flyers, marketing visuals). Use when the user wants Canva designs, brand graphics, or editable marketing assets — not for Kroki diagrams, Typst PDFs, Presenton/Marp decks, Excalidraw whiteboards, or HTML/UI (frontend-design / code_agent).
license: MIT (Chad skill). Workflow adapted from Apache-2.0 canva-sdks/canva-skills and community canva-design-director guidance.
requires: Canva MCP connected in Settings → Connectors (mcp-remote → https://mcp.canva.com/mcp). Invoke with call_mcp_tool(server_name="canva", tool_name=..., arguments=...).
runtime: node (mcp-remote) + Canva account (Free OK for core generate/edit/export)
---

# Canva graphic design

> Lineage: [canva-sdks/canva-skills](https://github.com/canva-sdks/canva-skills) (Apache-2.0) for MCP edit/feedback protocols;
> [canva-design-director](https://github.com/mavericraven/canva-design-director) for anti-slop layout discipline.
> Do **not** confuse with **Canvas LMS** (`canvas` native connector).

## When to use

| Need | Use |
|------|-----|
| Social post, poster, flyer, logo lockup, marketing graphic in Canva | **canva** |
| Structured flowchart / architecture | `kroki` → `render_diagram` |
| Hand-drawn whiteboard | `excalidraw` |
| Designed PDF/report | `typst` |
| Slide deck | `marp` or `presenton` |
| Local HTML/UI page | `frontend-design` + `delegate_to_code_agent` |

## How to call tools (critical)

Always use the MCP gateway:

```text
call_mcp_tool(
  server_name="canva",
  tool_name="generate-design",
  arguments={
    "query": "A bold vertical poster for Spikeball Club with high contrast typography and energetic layout",
    "design_type": "poster"   # REQUIRED: 'poster' | 'flyer' | 'instagram_post' | 'presentation' | 'doc' | 'card' | 'logo' | etc.
  }
)
```

**Required arguments for `generate-design`:**
- `query` (string): Description of what to create (copy, visual style, colors, elements).
- `design_type` (string): MUST be one of `'poster'`, `'flyer'`, `'instagram_post'`, `'presentation'`, `'doc'`, `'card'`, `'logo'`, `'infographic'`, `'invitation'`, `'business_card'`, `'youtube_thumbnail'`, `'facebook_post'`, `'twitter_post'`, etc. (Do not omit; do not use `dimensions`).

Discover exact tool names/schemas under the on-disk `mcp/canva/` index after connect (or via `search_for_tool`).

`load_skill('canva')` only injects this SOP — it does **not** prove the connector is up. Connectivity is proven by a successful `call_mcp_tool`. If that returns “not connected” / unknown server, tell the user to open **Settings → Connectors → Canva** — do not invent designs or fake download links, and do not loop on load_skill.

## Plan limits (be honest)

| Capability | Plan |
|------------|------|
| Generate, edit, search, export (standard quality) | **All plans (incl. Free)** |
| Monthly AI allowance for `generate-design` | Free ≈ 200 Standard / 20 Premium uses; Pro higher — not unlimited |
| Resize to new dimensions | **Pro+** |
| Lossless/transparent PNG, premium elements | **Pro+** (else export may fail `license_required`) |
| Autofill brand templates / brand kits | **Enterprise** — do not promise |

When Free AI allowance is exhausted, say so and offer Pro upgrade or a non-Canva path (Typst / HTML / Presenton).

## Agent workflow — create

1. `load_skill('canva')` once.
2. **Execute.** If the ask is already enough (“anime poster”, “Instagram sale post”), call `generate-design` immediately with a concrete brief you invent from context (theme, mood, title text, poster size). Prefer one good generation over a questionnaire.
3. At most **one** clarifying question, and only if a required choice would make the design wrong (e.g. which product, which event date). Do not ask theme/vibe/style as three separate questions.
4. Show the design link / thumbnail from the tool result.
5. If they want a file: `export-design` (PNG or PDF). Prefer PNG for social; PDF for print.
6. Share the Canva edit URL so they can refine in the editor.

Example brief for “make an anime poster”: dark high-contrast anime poster, vertical, bold title, dramatic lighting, no copyrighted character likeness unless the user named a specific licensed IP they own rights to use — use original characters / generic anime aesthetic when unsure.

## Agent workflow — edit (transaction protocol)

Every mutation uses this order (draft changes are **lost** if you never commit):

1. Resolve design ID (from URL segment after `/design/`, short link via resolve tool if available, or ask).
2. `start-editing-transaction` — show thumbnail(s); keep `transaction_id` + `pages`.
3. `perform-editing-operations` — batch ops; check `is_responsive` pages (restricted op set).
4. Preview + get explicit approval, then `commit-editing-transaction` — or `cancel-editing-transaction` to discard.

**API can:** replace/find text, format text (size/weight/style/color/align — not font family), update/insert/delete fills, position/resize elements, update title.

**API cannot:** new text boxes, background color changes, add/remove/reorder pages, font family, many shape/animation edits. Say those need the Canva UI.

## Agent workflow — review (read-only)

1. `get-design` + `get-design-thumbnail` (primary for layout/color) + `get-design-content` (copy).
2. Optional: start transaction only to inspect element geometry, then **cancel** (never commit).
3. Return prioritized feedback with concrete fixes. Hand off to the edit protocol if they want changes applied.

## Design quality bar

- One clear hierarchy (title / support / body) — avoid equal-weight clutter.
- Generous margins; whitespace is structure.
- Prefer brand kit colors when `list-brand-kits` is available; otherwise a tight palette (neutrals + one accent).
- No generic AI-slop fillers (random 3D blobs, unmotivated gradients, crowded sticker piles).
- Text on photos needs a solid overlay or slab for contrast.

## Do not

- Use any tool name other than `call_mcp_tool` with `server_name="canva"` for Canva actions.
- Use `load_skill` as a connectivity probe or loop on it.
- Confuse with Canvas LMS tools.
- Promise Enterprise autofill or Free unlimited AI generation.
- Commit edits without user approval.
- Use Canva for Kroki-style architecture diagrams or Typst reports.
- Claim an export succeeded if the tool returned `license_required` or a failed job.

## Reference

- Canva MCP docs: https://www.canva.dev/docs/mcp/
- Connectors setup: `docs/setup/CONNECTORS.md`
- Visual routing: `docs/features/VISUAL_OUTPUT.md`
