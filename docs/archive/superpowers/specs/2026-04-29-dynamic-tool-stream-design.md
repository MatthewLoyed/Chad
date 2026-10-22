# Spec: Dynamic Tool Stream (Claude-style)

## 🎯 Objective
Transform the chat stream from a static log into a dynamic, "Claude-like" experience. Tool calls should appear as branded, user-friendly activity blocks that clearly communicate Chad's actions (e.g., "Creating word doc..." instead of `create_word_doc`) and display appropriate application logos.

## 🏗️ Architecture

### 1. Centralized Tool Metadata (`src/clients/web/src/utils/tool-ui.tsx`)
A new utility to handle the transformation of raw tool names and parameters into user-friendly UI elements.
- **Function**: `resolveToolUI(event: ThinkingEvent)`
- **Output**: `{ title: string, icon: ReactNode, color: string }`
- **Logic**:
    - Map tool categories (Email, Browser, Filesystem) to colors and icons.
    - Extract parameters (e.g., `filename`, `query`) to create dynamic strings.
    - Handle specialist handoffs (delegation) with unique "Specialist" branding.

### 2. Enhanced Component (`ToolBubble.tsx`)
A complete overhaul of the `ToolBubble` component.
- **Layout**: Wide block with an icon on the left, label/status in the middle, and success/error indicator on the right.
- **State Management**: 
    - `pending`: Pulsing animation, "Working..." state.
    - `success`: Static, clean checkmark, "Completed" state.
    - `error`: Warning icon, error message, possible "Retry" action.
- **Animations**: Use `framer-motion` for entry (slide from bottom) and status transitions (fade-in checkmark).

### 3. Branded Experience
- **App Logos**: Support for high-quality SVG/Icon sets for:
    - Google Search / Brave Search
    - Gmail / Email
    - Google Docs / Word
    - File System / Local Terminal
    - Specialists (Communication, Workspace, etc.)
- **Delegation**: Distinct style for "Enlisting specialists" to signal a deeper reasoning loop.

## 🎨 Aesthetic & UX
- **Glassmorphism**: Translucent backgrounds with `backdrop-filter: blur(8px)`.
- **Dynamic Borders**: Subtle border-glow that matching the tool category color (e.g., blue for web, green for docs).
- **Responsive**: Clean layout that fits within the AI message bubble constraints.

## ✅ Success Criteria
- [ ] Tool calls are displayed as friendly messages (e.g., "Searching for recipes...") instead of snake_case tool names.
- [ ] Appropriate app logos appear next to the tool name.
- [ ] Delegation calls to specialist agents are clearly differentiated.
- [ ] Status transitions (pending -> success) are smooth and animated.
- [ ] No more tool calls are "hidden" or "generic" by default.
