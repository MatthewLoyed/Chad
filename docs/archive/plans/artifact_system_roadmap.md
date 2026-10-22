# Artifact System Research & Future Roadmap

## How The Nexus Works (Current)
The Nexus is a **Tabbed Workspace** integrated into the Right Panel. It works via an event-driven hook in the backend.

1.  **Trigger**: When Chad runs the `write_to_file` tool.
2.  **Intercept**: The backend handler (in `on_tool_start`) detects this tool and captures the `filename` and `content`.
3.  **Push**: This data is sent via bridge to `artifacts.js` on the frontend.
4.  **Render**: `nexus.js` automatically switches the tab to "ARTIFACTS", creates a glass-morphism card, and renders the content (Markdown or Code).

## Competitive Analysis (ChatGPT Canvas vs. Claude Artifacts)

| Feature | ChatGPT Canvas | Claude Artifacts | **The Nexus (Proposed)** |
| :--- | :--- | :--- | :--- |
| **Interface** | Split-screen "Notepad" | Dedicated Window | **Tabbed Panel** (Matches Sidebar) |
| **Editing** | Direct Highlighting | Reprompting / Iteration | **Diff View + File Watcher** |
| **Interact** | Comments / Shortcuts | React Rendering | **HTML Live Preview** |
| **Versioning** | Back Button / Branching | Conversation History | **Snapshots / Rollback** |

## Proposed Roadmap (Nexus V2)

### 1. Interactive Previews (HTML/React)
**Goal**: Allow HTML/JS files to be rendered in a safe `<iframe>` within the artifact card.
- **Benefit**: Instant visual verification of web generators.
- **Complexity**: Medium (Security focus).

### 2. Version Snapshots
**Goal**: When a file is overwritten, keep the old version accessible via a "History" dropdown.
- **Benefit**: "Undo" capability for Chad's changes.
- **Complexity**: Low.

### 3. "Canvas" Mode (Editability)
**Goal**: Allow the user to click into an artifact and *manually edit* the text before saving.
- **Benefit**: Human-in-the-loop correction before execution.
- **Complexity**: High (Frontend <-> Backend sync).

### 4. Smart Diffs
**Goal**: Instead of replacing the whole file, show a unified diff view (Red/Green lines).
- **Benefit**: Easier review of small changes in large files.
- **Complexity**: Medium.
