---
name: presenton
description: Generate polished AI slide decks (PPTX/PDF) via generate_presentation + self-hosted Presenton. Use when the user wants a branded deck, slideshow, or presentation from a topic, brief, or document — or when they accept your offer to "turn this into slides." Not for quick markdown slides (marp) or hand-editing an existing .pptx (pptx). Always load this skill BEFORE generate_presentation.
license: Apache-2.0 (Presenton); skill text MIT
requires: generate_presentation
runtime: docker
install: Settings → Services → Presenton, or docker compose -f docker/chad-services.yml --profile presenton up -d
---

# Presenton Presentation Skill

> Lineage: workflow patterns from [presenton/presenton](https://github.com/presenton/presenton) and community agent skills (REST + optional MCP at `/mcp`). Chad uses the native `generate_presentation` tool for artifact delivery.

## When to use

| Need | Use instead |
|------|-------------|
| **AI-designed deck** from topic/outline/doc | **presenton** → `generate_presentation` |
| User said yes to *"Want me to turn this into a slide deck?"* | **presenton** (after confirming topic + slide count) |
| Quick markdown → PDF/PPTX | `load_skill('marp')` → `render_marp` |
| Edit existing `.pptx` file | `load_skill('pptx')` |
| Diagram only | `load_skill('kroki')` → `render_diagram` |

## Prerequisites

1. Presenton enabled in **Settings → Services → Presenton** (URL + LM Studio model).
2. Sidecar healthy on `http://127.0.0.1:5000` (verify in Services health grid).

If unavailable, tell the user to enable Presenton in Settings — offer Marp as fallback.

## Agent workflow (mandatory)

1. **Confirm** topic, audience, and slide count (default **8**; short update 5–7, deep dive 15–20).
2. `load_skill('presenton')`
3. Write a clear **content** brief (bullets + goal — not a raw 20-page paste).
4. `generate_presentation(content=..., n_slides=8, template="general", export_as="pptx", title="deck-name")`
5. Return the download artifact; mention **`edit_url`** if they want to tweak slides in Presenton's UI.

### Example agent prompt (internal)

```
User wants a presentation on {topic}.

1. Confirm n_slides (default 8) and export (pptx vs pdf).
2. load_skill('presenton')
3. generate_presentation(
     content="Audience: …\nGoal: …\nOutline:\n- …",
     n_slides=8,
     template="professional",
     export_as="pptx",
     title="topic-slug"
   )
4. Share download_token / file; offer edit_url for manual polish.
```

## Template picker

| template | Best for |
|----------|----------|
| `general` | Default — balanced business/education |
| `modern` | Startups, product launches |
| `standard` | Clean executive decks |
| `swift` | Fast, minimal layouts |
| `report` | Data-heavy / analytical decks |
| `pitch-deck` | Investor or sales pitches |

Legacy aliases still accepted by the tool: `classic` → `general`, `professional` → `standard`.
Do **not** send `classic` or `professional` directly — Presenton v8 rejects them.

## export_as

| Value | When |
|-------|------|
| `pptx` | Editable in PowerPoint / Google Slides (default) |
| `pdf` | Sharing, printing, read-only distribution |

## Optional API fields

| Field | Notes |
|-------|-------|
| `theme` | e.g. `edge-yellow`, `professional-blue` |
| `language` | Default `English` |
| `visual.presenton_use_async` | v3 async poll (Settings) — long/cloud jobs |

Presenton also supports `tone`, `verbosity`, `instructions`, and document upload via its REST API — pass richer briefs in `content` for now.

## n_slides guidance

- Short update: **5–7** slides  
- Standard talk: **8–12**  
- Deep dive: **15–20** (cap 30 in tool schema)  
- Include title + closing slide in the count  

## Proactive offers (supervisor)

When the user describes an outline, talk, or report but has not asked for slides, **offer once**:

- *"Want me to turn this into a slide deck?"*
- *"I can generate a branded PPTX — roughly how many slides?"*

If they agree → follow this skill. If Presenton is down → suggest Marp or Typst PDF.

## Do not

- Do not call `generate_presentation` without `load_skill('presenton')` first.
- Do not use for diagram-only requests → `load_skill('kroki')`.
- Do not promise video export — not available yet.

## Reference

- [Presenton](https://presenton.ai/) · [MCP guide](https://docs.presenton.ai/v3/guide/generate-presentation-over-mcp)
- Chad: `docs/features/PRESENTON.md` · `docs/features/VISUAL_OUTPUT.md`
