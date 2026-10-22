---
name: kroki
description: Render text-based diagrams (flowcharts, sequence, architecture, ER, state) via render_diagram + Kroki. Use when the user asks for a diagram, chart, flowchart, sequence diagram, architecture map, or Mermaid/PlantUML output. Always load this skill BEFORE calling render_diagram — local models must follow the syntax rules here.
license: MIT (skill); Kroki is MIT
requires: render_diagram
runtime: python
install: docker run -p 8000:8000 yuzutech/kroki  # or use public https://kroki.io
---

# Kroki Diagram Skill

## When to use

- User wants a **structured** diagram — flowchart, sequence, architecture, ER, network (not a slide deck → use `marp`; not a designed PDF → use `typst`)
- The request maps to actual **diagram source code** (Mermaid, PlantUML, D2, GraphViz) — i.e. it has real nodes and edges
- **Not for illustrative/explainer visuals** — a court layout, "how X works," an infographic, anything without a natural flowchart shape. Kroki only draws structured graphs; forcing one of these into Mermaid produces a crude, wrong-shaped result. For those, skip this skill and call `build_html_file` with hand-written HTML/CSS/SVG instead.
- After `load_skill('kroki')`, call **`render_diagram`** — do not paste raw SVG guesses in chat

## Workflow (mandatory)

1. Pick `diagram_type` to match the language (see table below).
2. Write **only** the diagram source — no markdown fences inside `diagram_source`.
3. Call `render_diagram(diagram_source=..., diagram_type=..., output_format="svg")`.
4. If Kroki returns an error, **fix the source** and retry (common: bad quotes, missing semicolons in Mermaid).

## diagram_type picker

| User intent | diagram_type | Language hints |
|-------------|--------------|----------------|
| Flow / logic / branches | `mermaid` | `flowchart TD` or `graph LR` |
| API / messages over time | `mermaid` | `sequenceDiagram` |
| Classes / types | `mermaid` | `classDiagram` or `plantuml` |
| Architecture / C4 | `c4plantuml` or `plantuml` | C4-PlantUML macros |
| ER / database schema | `erd` or `mermaid` | `erDiagram` |
| Graph / network | `graphviz` | `digraph { ... }` |
| Modern declarative | `d2` | D2 syntax |

Default `output_format`: **svg** (scales well). Use **png** for slides; **pdf** for print.

## Mermaid rules (most common)

- Node IDs: camelCase, no spaces (`UserAuth` not `User Auth`)
- Edge labels with special chars: use quotes `-->|"O(1) lookup"|`
- One diagram per render call — split complex views into multiple files
- Prefer `flowchart TD` over deprecated `graph` when possible
- Sequence: declare `participant` names before use

## PlantUML rules

- Wrap in `@startuml` / `@enduml`
- For C4, use `c4plantuml` type with C4 includes as required by Kroki

## Self-host vs public

- **Public** (zero setup): `visual.kroki_url: https://kroki.io` in config
- **Self-host** (privacy): `docker run -p 8000:8000 yuzutech/kroki` → set `visual.kroki_url: http://127.0.0.1:8000`

## Do not

- Do not call `render_diagram` without loading this skill first on complex diagrams
- Do not use `pdf` skill for diagram generation
- Do not embed HTML/SVG by hand when Kroki can render validated source

## Reference skill lineage

Adapted from [uml-mcp-diagramming](https://github.com/antoinebou12/uml-mcp/blob/main/.skill/skills/uml-diagramming/SKILL.md).
