---
name: marp
description: Create slide decks from Marp markdown via render_marp. Use when the user wants slides, a deck, presentation, or markdown-to-PDF/PPTX/HTML slides. Always load this skill BEFORE render_marp. For editing existing .pptx files use pptx skill instead.
license: MIT (skill); Marp is MIT
requires: render_marp
runtime: node
install: npm install -g @marp-team/marp-cli
---

# Marp Slides Skill

## When to use

- Quick **slide deck** from outline or bullets
- Export to **PDF**, **PPTX**, or **HTML** slideshow
- **Not** for surgical .pptx edits → `load_skill('pptx')`

## Workflow (mandatory)

1. `load_skill('marp')`
2. Write Marp markdown (frontmatter + `---` slide breaks)
3. `render_marp(markdown=..., export_format="pdf", title="deck-name")`

## Minimal deck

```markdown
---
marp: true
theme: default
paginate: true
---

# Talk Title
Subtitle or author

---

## Agenda

- First topic
- Second topic

---

## Key Point

- Bullet one
- Bullet two

> Speaker note: optional note line
```

## Themes

Built-in: `default`, `gaia`, `uncover`. Set in frontmatter: `theme: gaia`.

## Slide rules

- Separate slides with a line containing only `---`
- Title slide: first slide after frontmatter; add `<!-- _class: lead -->` for centered hero slide
- Keep bullets short (4–6 max per slide)
- One idea per slide — split dense content

## Export format picker

| Format | When |
|--------|------|
| `pdf` | Sharing, printing (default) |
| `pptx` | Editable in PowerPoint |
| `html` | Browser presenter mode |

## Do not

- Do not call `render_marp` without Marp frontmatter (`marp: true`) — tool adds defaults but explicit frontmatter is better
- Do not use raw HTML slides when Marp covers the need

## Reference skill lineage

Workflow from [marp-mcp SKILL](https://github.com/masaki39/marp-mcp/blob/main/skills/marp/SKILL.md) and [marp-slide](https://github.com/softaworks/agent-toolkit/blob/main/skills/marp-slide/SKILL.md).
