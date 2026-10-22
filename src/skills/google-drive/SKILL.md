---
name: google-drive
description: Google Drive file access via the google-drive MCP server (list, read, export). Distinct from native Gmail/Workspace tools — use for Drive MCP tool names.
metadata:
  version: 1.0.0
  category: systems
  tags: [google-drive, mcp, files]
---

## Prerequisites
- **Google Drive MCP** connected in Settings → Connectors (OAuth credential + token paths in `secrets.yaml`).
- For Gmail send/search, use native tools + `load_skill('google_workspace')` instead.

## Workflow
1. `load_skill('google-drive')` before chaining multiple Drive MCP calls.
2. List or search before read/export when the user gives a filename not an ID.
3. Respect `excluded_tools` in config — some doc-editing tools may be disabled.

## Notes
- Large binaries may not inline — return links when export fails.
