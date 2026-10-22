---
name: brave-search
description: Real-time web search via the Brave Search MCP connector. Triggers on news lookup, current events, or when the user wants MCP-native search tools instead of Chad's built-in web_search.
metadata:
  version: 1.0.0
  category: research
  tags: [search, brave, mcp, web]
---

## Prerequisites
- **Brave Search MCP** connected in Settings → Connectors (`BRAVE_API_KEY` in `secrets.yaml`).
- Chad's built-in `web_search` (SearXNG) works without this connector — use Brave MCP when the user explicitly connected it or needs MCP tool semantics.

## Workflow
1. `load_skill('brave-search')` for multi-step research using Brave MCP tools.
2. Prefer a single focused query per tool call; refine if results are thin.
3. Cite sources from tool output; do not invent URLs.

## Notes
- Rate limits apply — avoid burst searching in a tight loop.
