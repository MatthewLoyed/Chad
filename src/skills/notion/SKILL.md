---
name: notion
description: Read and write Notion pages and databases via Notion MCP. Triggers on Notion page, database query, update blocks.
metadata:
  version: 1.0.0
  category: productivity
  tags: [notion, notes, mcp]
---

## Prerequisites
- **Notion MCP** connected in Settings → Connectors (`NOTION_API_TOKEN` in `secrets.yaml`).
- User must share target pages/databases with the integration.

## Workflow
1. `load_skill('notion')` before multi-step Notion edits.
2. Search or list before updating — confirm page/database IDs.
3. Prefer structured updates (append blocks) over wholesale rewrites.

## Notes
- Notion API is eventually consistent — re-read after large writes if needed.
