---
name: typst
description: Create designed PDFs (reports, memos, letters, invoices) via compile_typst + Typst. Use when the user wants a polished printable document with typography, tables, and sections — NOT for merging/OCR/existing PDF utilities (use pdf skill). Always load this skill BEFORE compile_typst.
license: MIT (skill); Typst is Apache-2.0
requires: compile_typst
runtime: python
install: winget install Typst.Typst
---

# Typst PDF Skill

## When to use

- **Designed** PDF output: report, memo, letter, invoice, one-pager
- User says "professional PDF", "formatted document", "print-ready"
- **Not** for: split/merge PDFs, OCR, form-fill → `load_skill('pdf')`

## Workflow (mandatory)

1. `load_skill('typst')` (this document)
2. Draft Typst source (`.typ` syntax) — see patterns below
3. `compile_typst(source=..., title="short-name")`
4. On compile error: read stderr, fix line cited, retry

## Minimal document pattern

```typst
#set page(paper: "us-letter", margin: 1in)
#set text(font: "Libertinus Serif", size: 11pt)
#set heading(numbering: "1.")

= Report Title
_Author · Date_

== Section One
Body text with *bold* and _italic_.

#table(
  columns: (1fr, 1fr),
  [*Item*], [*Value*],
  [Alpha], [1],
  [Beta], [2],
)
```

## Templates in repo

Copy and adapt from `src/skills/typst/templates/`:

- `memo.typ` — internal memo
- `letter.typ` — business letter

## Typst gotchas

- Headings use `=`, `==`, `===` (not markdown `#`)
- Raw `#` starts markup — escape or use strings carefully
- Tables: `#table(columns: (...), ...)` with row cells as separate args
- Images: `#image("path.png", width: 80%)` — paths relative to compile dir (`artifacts/`)
- Page breaks: `#pagebreak()`

## vs other tools

| Need | Tool / skill |
|------|----------------|
| Designed PDF | **typst** + `compile_typst` |
| PDF utilities | `pdf` skill |
| Slide deck | `marp` + `render_marp` |
| Diagram | `kroki` + `render_diagram` |

## Reference skill lineage

Patterns informed by [typst-claude-skill](https://github.com/ChanMeng666/typst-claude-skill) and [claude-skill-typst](https://github.com/lucifer1004/claude-skill-typst).
