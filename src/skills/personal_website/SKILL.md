---
name: personal_website
description: "Turn the user's personal data into a beautiful, self-contained web page — a visual roadmap, timeline, life dashboard, goal tracker, portfolio, or 'year in review'. Use when the user wants to visualize their own data as a site/page/dashboard/roadmap/timeline, or asks to 'make a website of my X' or 'turn this into a visual'. Emphasizes aesthetics and clear visual storytelling."
version: 1.0.0
license: MIT
tags: [website, visualization, roadmap, dashboard, design, personal]
---

# Personal Data → Website

Turn the user's data (goals, timeline, projects, stats, a collection) into a **distinctive,
aesthetic web artifact** that tells a visual story. This skill is the **creative brief +
data plan**; the actual build is delegated to the design skills. Your job is taste and
structure, theirs is pixels.

## When to use
- "Make a website / page / dashboard of my <goals / trip / reading / projects>."
- "Turn this into a visual roadmap / timeline."
- "I want to *see* my <data> nicely, not as a list."

For a plain document use `docx`/`pdf`/`typst`. For a one-off diagram use `kroki`. This skill
is for **rich, styled, browsable web pages**.

## Pipeline

### 1. Get the data
Where does the content come from?
- User pasted / described it → use that.
- It lives in the user's accounts → gather it first with **`personal_context`** (emails,
  Drive docs, GitHub projects).
- Confirm you have the real data before designing — don't invent milestones.

### 2. Choose the visual format to fit the data
| Data shape | Format |
|---|---|
| Steps toward a goal, phases over time | **Roadmap** (milestones on a path) |
| Dated events / a life story | **Timeline** |
| Metrics / current status at a glance | **Dashboard** (cards, stats) |
| A collection (books, trips, projects) | **Gallery / grid** |
| Progress toward targets | **Tracker** (bars, rings) |

State the chosen format and a one-line layout concept before building.

### 3. Design with taste (the part the user cares about)
- **Distinctive, not generic-AI.** Commit to a point of view — a real palette (2–3 colors +
  neutrals), intentional type pairing, generous whitespace.
- **Clear hierarchy.** One focal element per screen; the eye should know where to start.
- **Roadmaps/timelines read directionally** — left→right or top→down, milestones clearly
  marked, current position obvious.
- **Cohesion over decoration.** Consistent spacing, corners, shadows. Restraint reads as
  premium; clutter reads as a template.
- Responsive and self-contained so it just opens in a browser.

### 4. Build — delegate to the design skills
Hand the brief (data + format + design direction) to the builder:
- **`frontend-design`** (→ `code_agent`) for a polished, custom-coded page — the default for
  anything with real visual ambition.
- **`web-artifacts-builder`** for a quick self-contained HTML artifact.

Pass them the concrete content and the design direction from step 3 — not just "make a
website." The quality of the brief sets the ceiling on the result.

### 5. Deliver & iterate
Show it, then invite one round of refinement ("want the palette warmer, or the milestones
bigger?"). Aesthetic work is iterative — expect a tweak pass.

## Local-model notes
- **This skill plans; the subagent builds.** Don't try to hand-write a large styled page
  inline — write the brief and delegate to `frontend-design`/`code_agent`, where the heavy
  generation happens in its own context.
- Lock the data and the format choice **before** designing, so the build is a single clean
  pass rather than a re-do.

## Common pitfalls
- ❌ Designing before you have the real data (inventing milestones/stats).
- ❌ Generic AI aesthetic — centered everything, default blues, no point of view.
- ❌ Handing the builder "make a website" instead of a concrete brief.
- ❌ Cramming every data point in — curate for the story.
