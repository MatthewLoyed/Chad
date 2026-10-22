# Design Spec: Chad HUD (Heads-Up Display)
Date: 2026-04-29
Topic: Chad HUD (Global Command Bar)

## 1. Overview
The **Chad HUD** is a minimalist, global command interface designed to reduce context-switching friction. It provides a "bottom-middle" screen overlay triggered by a global hotkey, allowing users to issue commands and receive brief status updates without opening the full purple GUI.

## 2. Requirements & Goals
- **Global Accessibility**: Triggerable from any application via `Alt + Space`.
- **Minimalist UX**: Follows a "Wispr-like" flow—clean input, subtle feedback, and short results.
- **Visual Feedback**: Uses a pulsing purple glow around the bar while Chad is processing.
- **Toggle Behavior**: Pressing the hotkey toggles visibility. Hiding the HUD while it's working does NOT cancel the task.
- **Deep Integration**: Seamlessly routes "more detail" to the Main GUI while showing "summary" on the HUD.

## 3. Architecture & Components

### 3.1 GUI Window (`HUDWindow`)
- **Type**: Frameless, transparent, `Always on Top` window using `pywebview`.
- **Placement**: Primary monitor, centered horizontally, bottom 10% vertically.
- **Style**: Dark theme, purple accents, glassmorphism/blur (OS permitting).

### 3.2 Logic Controller (`HUDController`)
- **Hotkey Listener**: Uses `pynput` or native GUI hooks to capture `Alt + Space`.
- **Focus Management**: Automatically grabs keyboard focus when opened.
- **State Machine**:
    - `HIDDEN`: Process is dormant or window is transparent/hidden.
    - `INPUT`: Visible, focused, awaiting user command.
    - `PROCESSING`: Pulse animation active, command sent to Brain.
    - `RESULT`: Shows short text response (max 120 chars).
    - `TIMEOUT`: Auto-hides after 5s of result display.

### 3.3 Event Bridge Extensions
- The `EventBridge` will be updated to broadcast events to both `MainWindow` and `HUDWindow`.
- A new `origin` flag in messages will allow the Brain to optimize responses for the HUD (e.g., "Give me a 1-sentence summary for the HUD").

## 4. User Interaction Flow
1. **Trigger**: User hits `Alt + Space`. HUD appears at bottom-middle.
2. **Command**: User types "remind me to buy milk in 10 mins" and hits `Enter`.
3. **Action**: HUD starts pulsing purple. The command is sent to the Brain.
4. **Visibility Toggle**: User hits `Alt + Space` again. HUD disappears, but the reminder task continues.
5. **Completion**: If the HUD is visible, it shows "Reminder set for 10:45 PM." If hidden, it stays hidden (result appears in Main GUI).

## 5. Technical Implementation Details
- **Frontend**: A minimal React/Tailwind component (or vanilla HTML/CSS) hosted as a separate route or file.
- **Communication**: Uses the existing `WebViewAPI` bridge pattern for Python-JS communication.
- **Task Cancellation**: The "Stop" button in the Main GUI remains the primary way to cancel tasks; the HUD toggle is visibility-only.

## 6. Testing Plan
- **Verification**: Ensure the HUD correctly grabs focus even if a full-screen app (non-game) is active.
- **Performance**: Monitor memory usage of the dual-webview setup.
- **Conflict Check**: Ensure `Alt + Space` doesn't conflict with common IDE/OS shortcuts (or allow user to remap).
