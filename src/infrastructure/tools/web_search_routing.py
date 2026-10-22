"""Shared web-search routing guidance — keep supervisor, handoffs, and docs aligned."""
from __future__ import annotations

# Query STRATEGY (distinct from routing). The #1 live deep-research failure was the model
# firing over-specific exact-quote queries (e.g. `"The Valley Venues" Wildwood GA "preferred
# caterer"`) that returned zero results, then repeating near-identical ones instead of
# broadening — so a step that needed one fact never got it. Injected wherever web search is used.
WEB_SEARCH_STRATEGY = (
    "SEARCH STRATEGY — start broad, then narrow, and recover from empty results:\n"
    "- If a query returns no/empty results, do NOT repeat it or retry a near-identical one. "
    "BROADEN: drop exact-quote (\"...\") operators, remove rare qualifiers, and try the plain "
    "entity name.\n"
    "- To get a specific business/place's own facts (prices, policies, menu), search its NAME "
    "to find its official site, then read THAT page directly — don't guess at quoted policy "
    "phrases a search engine is unlikely to index.\n"
    "- Vary the angle across queries (name, location, reviews, official site) rather than "
    "re-asking the same thing; two independent sources beat five re-phrasings of one."
)

# Research is a SINGLE path (#510): quick facts via web_search yourself; anything deeper via
# delegate_to_deep_research_agent, which owns discovery + parallel depth research + synthesis
# internally (there is no serial delegate_to_research_agent, and the main supervisor never
# touches research_discover/parallel_research directly).
WEB_SEARCH_ROUTING_SUPERVISOR = (
    "- **Quick web lookup** (one topic, 1–2 queries, simple fact or link) → `web_search` yourself.\n"
    "- **Deep / multi-item research** (several items, comparisons, buy links for a list, a synthesized "
    "report — or even one item needing real depth) → `delegate_to_deep_research_agent` ONCE with the "
    "full research goal: what to find, how many items, and the fields/format you need. It handles "
    "discovery (if you don't already know the item names), parallel depth research, and synthesis "
    "internally, and returns ONE clean report. Never research items yourself one at a time.\n"
    "- Never call `brave_web_search` or `fetch_html` yourself — those are internal; use `web_search` "
    "or `delegate_to_deep_research_agent`.\n"
    "- **YouTube in chat** → `web_search` with `site:youtube.com` and `fetch_content=false`, then "
    "`show_youtube_video` with the `video_id`/URL and title from results.\n"
    "- If `web_search` results for this exact question come back stale, generic, or snippet-only "
    "(no real page content) — do NOT keep retrying it. Switch to `delegate_to_browser_agent` after "
    "ONE failed attempt; see its description for when it beats web_search.\n"
    + WEB_SEARCH_STRATEGY
)

# Criteria for browser_agent vs. web_search (and vs. Drive/email/other MCP-backed specialists).
# web_search is the default for any public, static, search-indexable fact — try it first and only
# once or twice. browser_agent is for real interactive DOM control; escalate to it rather than
# looping web_search when any of the bullets below apply.
# Product-name gate: if the user names a specific web app/inbox and we lack a tool for THAT
# product, browser wins over a "close" API (Gmail≠Outlook) or public web_search (private chats).
BROWSER_AGENT_HANDOFF_DESC = (
    "Drives the user's real, already-logged-in Chrome via Playwright: navigates, clicks, fills forms, "
    "and reads live DOM state. DEDICATED TOOLS FIRST: if a native/API tool supports the requested "
    "operation in the exact product, use that tool instead of browser automation. Discover it with "
    "search_for_tool if needed. For a new Gmail draft use create_gmail_draft; for a reply draft use "
    "draft_gmail_reply. Being able to visit a page is not a reason to bypass a dedicated tool. "
    "Use the browser only for operations the exact product's tools cannot perform. Use when:\n"
    "- PRODUCT-NAME / NAMED SURFACE (when no suitable exact-product tool exists): the user names a specific web app or inbox "
    "(Outlook, ChatGPT, The Knot, Slack web, Notion, banking portal) — do not substitute search_gmail or "
    "web_search because an API looks close; only use an exact product tool.\n"
    "- Authenticated pages: SaaS dashboards, billing, or accounts requiring the user's existing login.\n"
    "- Interactive multi-step flows: forms, dropdowns, filters, or multi-page navigation.\n"
    "- Stale or failed web_search: escalate here after one failed web_search attempt.\n"
    "Do NOT use for Google Drive/Calendar/Docs (use traversal/native tools), or Canvas LMS (use canvas_agent). "
    "Pass the complete browsing objective and direct URL in `task` — do not break work into single clicks."
)

BROWSER_AGENT_SUPERVISOR_ROSTER = (
    "Dedicated native/API tools take priority for supported operations in the exact product "
    "(new Gmail draft → create_gmail_draft; reply draft → draft_gmail_reply). Discover missing tools "
    "with search_for_tool before choosing browser automation. Reads and interacts with a real, "
    "logged-in Chrome browser only for operations those tools cannot perform: named web apps/inboxes the user "
    "cited (Outlook, ChatGPT, The Knot, …) when no API exists for that exact product; SaaS "
    "dashboards; sites needing clicks/forms/pagination; one-site-only tasks; or auto read-only "
    "verify when APIs cannot settle signed-in web state and a deep link/page goal exists. "
    "Escalate after one failed `web_search` when results are stale/insufficient."
)

# deep_research_agent (#510) is THE research delegate — it owns discovery, parallel depth
# research, and synthesis internally (research_discover/parallel_research are its own private
# tools, never bound to the main supervisor). Description used as its handoff tool's own schema
# text lives in handoffs.py; this is the shorter roster-line version.
DEEP_RESEARCH_AGENT_SUPERVISOR_ROSTER = (
    "Owns the whole research workflow — discovers item names when you don't already have them, "
    "researches them all CONCURRENTLY, and returns one synthesized report/comparison. The only "
    "research delegate; for one quick fact, use `web_search` yourself instead."
)

# Kept for back-compat imports.
RESEARCH_AGENT_HANDOFF_DESC = DEEP_RESEARCH_AGENT_SUPERVISOR_ROSTER
RESEARCH_AGENT_SUPERVISOR_ROSTER = DEEP_RESEARCH_AGENT_SUPERVISOR_ROSTER
PARALLEL_RESEARCH_SUPERVISOR_ROSTER = DEEP_RESEARCH_AGENT_SUPERVISOR_ROSTER

SCOUT_WEB_SEARCH_NOTE = (
    "Quick fact → `web_search` (1–2 queries). Multi-item or open-ended research → "
    "`delegate_to_deep_research_agent` with the full research goal (it handles discovery, "
    "parallel depth research, and synthesis internally). "
    "If a query returns nothing, broaden it (drop quotes/rare qualifiers) — don't repeat it."
)
