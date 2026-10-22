# Tools Directory Organization

## Overview

All Chad's tools are organized by **category/scope** at the top level. Each directory = one tool scope.

> [!WARNING]
> **MCP Infrastructure**: Any references to MCP (Model Context Protocol) in the registry or discovery logic are currently **DEAD CODE**. Chad does not use MCP in the current implementation.

---

## 📂 Directory Structure

```
src/tools/
├── canvas/           ← Canvas LMS tools (15 tools)
├── filesystem/       ← File operations (8 tools)
├── messaging/        ← Messaging tools (e.g., Telegram)
├── monitoring/       ← System monitoring & diagnostics
├── smart_home/       ← Hubspace / Smart Home control
├── workspace/        ← Google Workspace (Gmail, Docs, etc.)
├── media/            ← Media control (1 tool)
├── time/             ← Timers, alarms, reminders (4 tools)
├── system/           ← OS-level operations (15 tools)
└── web/              ← Web browsing, search (3 tools)
```

---

## 🎯 Tool Categories

### Canvas (15 tools)
**Scope**: Canvas LMS integration
**Location**: `src/tools/canvas/`

### Filesystem (8 tools)
**Scope**: Local file operations
**Location**: `src/tools/filesystem/`

### Workspace (14+ tools)
**Scope**: Google Workspace (Gmail, Docs, Calendar, Drive, Contacts)
**Location**: `src/tools/workspace/`

### Messaging (1 tool)
**Scope**: External messaging platforms (Telegram)
**Location**: `src/tools/messaging/`

### Monitoring (1 tool)
**Scope**: Performance and system health tracking
**Location**: `src/tools/monitoring/`

### Smart Home (1 tool)
**Scope**: IoT and Smart Home device control (Hubspace)
**Location**: `src/tools/smart_home/`

### System (15 tools)
**Scope**: OS-level operations, utilities
**Location**: `src/tools/system/`

---

## 🤖 Specialist Agent Mapping

In the **Hierarchical Orchestration** system, tools are dynamically assigned to specialists based on their categories.

| Specialist Agent | Assigned Tool Categories |
|------------------|--------------------------|
| **Communication** | `messaging`, `workspace` (Gmail/Slack) |
| **Workspace** | `workspace` (Drive/Docs/Sheets/Calendar), `canvas` |
| **UI Automation**| `system` (GUI Control), `monitoring` |
| **Browser** | `web` |
| **Code** | `system` (Terminal/Shell), `filesystem` |
| **System** | `system` (OS Settings), `time`, `smart_home` |
| **Memory** | `memory`, `meta` |

> [!NOTE]
> This mapping is controlled in `src/infrastructure/tools/registry.py` via the `get_tools_by_domain` method.

---

## 🔧 Skills (Multi-tool Agents)

**Location**: `src/skills/`

- **homework_assistant**: Canvas + Google Docs integration.
- **morning_briefing**: Gmail + Calendar integration.
- **organize_folder**: Filesystem operations.

---

## 📊 Tool Discovery

**How it works**:
1. `UnifiedRegistry.discover_all()` scans `src/tools/` at startup.
2. `FilesystemScanner` walks each top-level directory.
3. Category name = directory name (e.g., `canvas/` → `canvas` category).
4. Tools auto-register with their category.

**Current totals**:
- **70+ tools** discovered
- **10+ categories** (canvas, filesystem, workspace, system, web, messaging, monitoring, smart_home, etc.)
- **3 skills**

---

## 📝 Import Conventions

**Correct imports**:
```python
# Canvas tools
from src.tools.canvas.courses import GetCanvasCourses

# Workspace tools
from src.tools.workspace.gmail import SearchGmailTool

# Filesystem tools
from src.tools.filesystem.file_manager import FileManagerTool
```

---

**Last updated**: 2026-04-10 (Cleaned & Updated branding)
