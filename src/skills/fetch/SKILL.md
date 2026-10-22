---
name: fetch
description: Fetch and extract text from URLs via the Web Fetch MCP server. Triggers on read this page, pull content from URL, parse article at link.
metadata:
  version: 1.0.0
  category: research
  tags: [fetch, web, mcp, url]
---

## Prerequisites
- **Web Fetch MCP** connected in Settings → Connectors (no API key; off by default).

## Workflow
1. `load_skill('fetch')` when you need raw page text from a specific URL.
2. Prefer Chad's `web_search` for discovery; use fetch for a known URL.
3. Respect robots/paywalls — report fetch failures plainly.

## Notes
- Does not execute JavaScript-heavy SPAs well; use Playwright MCP for interactive pages.
