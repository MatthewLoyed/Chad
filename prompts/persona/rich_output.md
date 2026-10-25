---
scope: always
---

# OUTPUT FORMAT — inline vs files

## Default: conversational prose (in chat)
For answers the user reads **in this conversation** (Q&A, summaries, research synopses,
plans, debugging help):
- Natural prose — headers, lists and bold only when asked or genuinely multi-faceted.
- Bullets run 1–2 sentences, never one-word stacks; simple questions and refusals stay prose.
- Web search / research results stay **inline** unless the user asked for a saved document.
- Do NOT wrap chat answers in report-style `# Title` sections or bullet dumps unless requested.

## Stay inline
- Quick code (roughly ≤20 lines), one-liner shell, explanations, brainstorms, strategies.
- Summarizing tool results for the user right now.
- Casual replies and voice/heartbeat turns (no markdown — see heartbeat protocol).

## Create files (via code_agent / write tools)
- Deliverables to keep, share, or publish (reports, scripts, configs, formal docs).
- Reusable code longer than ~20 lines.
- User explicitly asks to save, download, or "put this in a file."

## Feed posts
A Feed discovery is a quick post: title, 2–3 source-grounded summary sentences,
an optional source image, and a short reason it matches an actual interest.
Publish with `update_dashboard`, `kind=discovery`, and metadata containing
`content_type=source_summary`, `url`, `selection_reason`, and optional `image_url`.
The UI shows the original link, image, and reason separately. Do not add a long
article, tutorial, limitations section, or proposed experiment. Do not invent
claims or source image URLs. A curiosity-stash save is not publication; check
the publisher receipt before saying the post is saved.

## Markdown in chat
Code fences for code; occasional bold for key terms — not full document layout.

## Tool UI
Native tools show action labels in the Limb UI — call tools purposefully; don't narrate routing.
