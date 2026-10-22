# Web Search — Rich Media (Future)

> **Status:** All 5 phases implemented. Tracks the evolution from text-only
> `web_search` results to multimodal research output (images, video, previews).

## Goal

Chad should return **useful multimodal context**, not just prose:

- Product/vendor **hero images** and gallery thumbnails
- **YouTube/Vimeo** embed URLs + titles (partially covered today via `fetch_content=false` + `show_youtube_video`)
- **OG/Twitter card** preview images for links in research summaries
- Optional **screenshot** of the rendered page when text extract is thin (ties into Playwright Tier-2)

## Proposed shape (tool result)

```json
{
  "url": "https://example.com/",
  "title": "Example Vendor",
  "content": "…markdown prose…",
  "content_source": "playwright:trafilatura_precision",
  "media": {
    "images": [{"url": "https://…/hero.jpg", "alt": "…", "role": "og:image"}],
    "videos": [{"url": "https://youtube.com/watch?v=…", "title": "…", "platform": "youtube"}]
  }
}
```

The Limb/React UI would render `media.images` inline in tool cards (same pattern as `tool-ui.tsx` formatters).

## Implementation phases

1. **Metadata pass (cheap)** — DONE. `src/tools/web/media_metadata.py::parse_media_metadata()` parses `<meta property="og:*">`/Twitter card images + JSON-LD `VideoObject` blocks from fetched HTML (no new fetch tier), hooked into `fetch_pipeline._finalize()` and propagated through `web_search.py`'s per-source `media` field.
2. **Playwright enrichment** — DONE. `parse_media_metadata(..., dom_image_fallback=True)` runs only when `fetch_pipeline._finalize()` was called with `source_prefix == "playwright"` — i.e. against JS-rendered DOM, where lazy-load `data-src`/`srcset` attributes are already resolved. `_extract_dom_hero_image()` heuristically scores `<img>` candidates (size, `<main>`/`<article>`/`<figure>` ancestry) and falls back only when no OG/Twitter image was found.
3. **Dedicated video routing** — DONE. `src/tools/web/video_routing.py` detects when a *search hit itself* is a YouTube/Vimeo watch URL and calls the platform's public oEmbed endpoint (no key) for an authoritative title/thumbnail, skipping the HTML fetch tiers entirely (video SPA pages are bot-walled/junk for trafilatura). Wired as an early-exit branch in `fetch_pipeline.resolve_url_content()`; falls through to normal tiers if oEmbed fails (private/deleted video).
4. **UI** — DONE. `src/clients/web/src/utils/webSearchMedia.ts::resolveWebSearchMedia()` parses the `media` field out of a completed `web_search` tool event's JSON result; `WebSearchMediaStrip.tsx` renders an image strip + video chips, wired into `ToolBubble.tsx`'s expanded details section (below the console-output JSON dump).
5. **Evals** — DONE (diagnostic, not CI-free-lane — matches the existing `expect_playwright` pattern which also requires live network). `scripts/diagnostics/benchmark_web_extraction.py` now reports per-case `media_images`/`media_videos` counts and flags `expect_og_image` mismatches; a few catalog entries can carry `"expect_og_image": true`.

## Related issues

- #257 — tiered fetch (Playwright / stealth) — **in progress** on extraction PR
- Future issue: **Web search rich media** (images/video/previews) — #331

## Non-goals (v1)

- Downloading/hosting images on the Brain (URLs only)
- Full browser_agent delegation for every search hit
- Firecrawl paid stealth (separate #257 tier-3)
