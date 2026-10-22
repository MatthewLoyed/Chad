---
name: animation_preview
description: Render animated SVG/HTML/CSS with headless Chromium via render_animation so you can see what you wrote — single frame, a filmstrip over time, or a pixel diff between two versions. Use when writing or debugging CSS/SVG animations, keyframes, or transitions. Load this skill BEFORE calling render_animation.
license: MIT
requires: render_animation
---

# Animation Preview Skill

## When to use

- You wrote or edited **SVG/CSS/HTML animation** (keyframes, transitions, transforms) and need to see the actual rendered result before calling it done
- User asks to **QA an animation**, compare before/after, or wants a static preview of an SVG/HTML snippet
- Not for static, non-animated diagrams — use the `kroki` skill for flowcharts/sequence/ER diagrams instead

## Tool: `render_animation`

One tool, four modes via the `mode` param:

| mode | Use for | Key params |
|------|---------|------------|
| `frame` (default) | Single screenshot at one moment — "does this look right at all?" | `delay_ms` (when to capture) |
| `filmstrip` | Several screenshots over time composed into one strip — "does the motion look right?" | `frames` (2-8), `duration_ms`, `show_timestamps` |
| `diff` | Pixel-compare a before/after pair — "did my edit do what I meant?" | `compare_html` (the "after" version; `html` is "before") |
| `video` / `mp4` | Export the animation as an MP4/WebM video artifact | `duration_ms` (length to record), `title`, `viewport_width`, `viewport_height` |

## Workflow

1. Write the `html` — **self-contained**: inline `<style>`/CSS and keyframes, no external asset references (fonts, images) it can't resolve.
2. Pick the mode:
   - Quick look → `render_animation(html=..., mode="frame")`
   - Check timing/motion → `render_animation(html=..., mode="filmstrip", frames=4, duration_ms=1000)`
   - Confirm an edit changed the right thing → `render_animation(html=before, mode="diff", compare_html=after)`
3. Inspect the returned artifact. If it doesn't match intent, fix the source and re-render — don't guess twice.
4. Prefer `diff` over two separate `frame` calls when iterating on an existing animation — it directly surfaces `percent_changed` instead of making you eyeball two images.

## Notes

- Runs entirely on this project's existing `playwright` + `Pillow` Python dependencies (see `requirements.txt`) — no new external service, no third-party MCP server.
- Uses an ephemeral headless Chromium instance shared across calls in-process; first call may be slightly slower (browser launch).
- `delay_ms` controls how far into the animation the `frame`/`diff` screenshot is taken — 0ms captures the very first paint, which is rarely what you want for a CSS transition.

## Do not

- Do not use this for non-animated diagrams — that's `kroki` (`render_diagram`)
- Do not assume an animation is correct without rendering it — CSS/SVG timing bugs are invisible in source
- Do not reference external URLs/fonts/images in `html` — Chromium renders it standalone with no network access guarantees
