---
name: todoist
description: Task and project management via Todoist MCP. Triggers on add task, list todos, deadlines, project organization.
metadata:
  version: 1.0.0
  category: productivity
  tags: [todoist, tasks, mcp]
---

## Prerequisites
- **Todoist MCP** connected in Settings → Connectors (`TODOIST_API_TOKEN` in `secrets.yaml`).

## Workflow
1. `load_skill('todoist')` before creating or reorganizing many tasks.
2. Confirm project names with a list/search tool when the user is vague.
3. When completing work, mark tasks done rather than deleting unless asked.

## Notes
- Use clear task titles; put context in descriptions, not titles.
