# Implementation Plan: Chad HUD
Date: 2026-04-29
Spec: [2026-04-29-chad-hud-design.md](../specs/2026-04-29-chad-hud-design.md)

## Phase 1: Frontend Foundation (React)
- [ ] **Create HUD Component**: Create `src/clients/web/src/components/HudView.tsx`.
    - Minimal input bar with purple glow (`animate-pulse`).
    - Glassmorphism styling (`backdrop-blur-md`).
    - Single-line response display with scrolling.
- [ ] **Routing/Conditional Rendering**: Update `src/clients/web/src/App.tsx` or `main.tsx` to handle a `/hud` query parameter or route.
    - Example: `http://localhost:5173/?mode=hud`.
- [ ] **HUD State Store**: Update `src/clients/web/src/store.ts` to manage HUD-specific visibility and result states.

## Phase 2: Desktop Infrastructure (Python)
- [ ] **Create HUD Controller**: Create `src/clients/desktop/hud_controller.py`.
    - Initialize a secondary `pywebview` window.
    - Implement `toggle_visibility()` method.
    - Setup window placement at bottom-middle of primary monitor.
- [ ] **Hotkey Integration**: 
    - Use `pynput.keyboard` to listen for `Alt + Space`.
    - Ensure the listener runs in a separate thread to avoid blocking the GUI.
- [ ] **App Controller Hook**: Update `src/clients/desktop/app_controller.py`.
    - Instantiate `HUDController` during initialization.
    - Handle lifecycle (shutdown) for the HUD window.

## Phase 3: Communication Bridge
- [ ] **Event Bridge Broadcast**: Update `src/clients/desktop/event_bridge.py`.
    - In `on_thinking_update`, `on_tool_end`, etc., push data to both windows if they exist.
- [ ] **Origin Tracking**: 
    - Add `origin` field to the payload sent from the HUD.
    - Ensure `EventBridge` identifies requests from the HUD as needing "Short Responses."

## Phase 4: Polish & Performance
- [ ] **Animation Sync**: Ensure the purple pulse in React starts/stops exactly when the Python bridge reports tool execution.
- [ ] **Focus Recovery**: Ensure that when the HUD is hidden, keyboard focus returns to the previous application.

## Verification Tasks
- [ ] Run `python main_gui.py` and verify `Alt + Space` triggers the window.
- [ ] Verify HUD appears at the bottom-middle.
- [ ] Send "hello" via HUD and verify pulse + short response.
- [ ] Toggle HUD while a task is running and verify it doesn't interrupt the task.
