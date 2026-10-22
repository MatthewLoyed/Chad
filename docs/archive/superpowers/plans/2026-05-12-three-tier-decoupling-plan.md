# 📋 Implementation Plan: 3-Tier Architecture Decoupling

## 1. Objective

This plan outlines the step-by-step engineering tasks required to implement the 3-tier decoupled architecture (**Brain, Hands, and Eyes**) as defined in [2026-05-12-three-tier-decoupling-design.md](../specs/2026-05-12-three-tier-decoupling-design.md). 

---

## 2. Phase 1: Extracting "The Hands" (OS Daemon)

### 📝 Task 1.1: Define the Primitive Message Schema
*   **Target File**: `src/daemons/protocol.py` (NEW)
*   **Task**: Establish a typed protocol schema for OS primitives to ensure absolute interface parity.
*   **Details**:
    *   Use Pydantic or simple `dataclass` objects for serializing payloads.
    *   Structure: `{ "request_id": str, "action": str, "payload": dict, "timeout": float }`

### 📝 Task 1.2: Construct the Invisible OS Daemon Daemon
*   **Target File**: `src/daemons/hands.py` (NEW)
*   **Task**: Build the standalone python client that connects to the Brain.
*   **Details**:
    *   Uses standard `websockets` or `aiohttp` to connect to `ws://localhost:8767/ws/daemon`.
    *   Pushes the local `CHAD_SERVER_TOKEN` in headers / AUTH frame for robust security.
    *   Maintains an internal mapping of **OS primitives** to executing methods.
    *   **CRITICAL**: Executes `pyautogui` / Windows calls inside an `asyncio` threadpool executor (`run_in_executor`) so heavy OS blocking doesn't lock up the websocket heartbeat listener.
    *   *Permitted imports*: `pyautogui`, `pygetwindow`, `comtypes`.

---

## 3. Phase 2: Upgrading "The Brain" (Server Core)

### 📝 Task 2.1: Expose Multiplexed WebSocket Router
*   **Target File**: `src/remote/server.py`
*   **Task**: Introduce dedicated endpoints `/ws/daemon` and `/ws/gui`.
*   **Details**:
    *   Upgrade the existing `Starlette` routes to support separate listeners.
    *   Refactor standard client tracking so the Server knows exactly when 0, 1, or >1 Daemon is attached.

### 📝 Task 2.2: Build the `DaemonExecutorBridge`
*   **Target File**: `src/remote/daemon_bridge.py` (NEW)
*   **Task**: Create the server-side manager that handles sending commands to connected Hand daemons.
*   **Details**:
    *   Maintains an `asyncio.Future` lookup map `pending_requests: Dict[str, asyncio.Future]`.
    *   Implements an aggressive timeout logic (e.g., `asyncio.wait_for(..., timeout=2.5)`).
    *   Exposes `is_ready` attribute. If `False`, immediately raises `DaemonOfflineError`.

### 📝 Task 2.3: Adapt the Core System Tool Runner
*   **Target File**: `src/infrastructure/tools/tool_executor.py` (or current `HybridToolExecutor`)
*   **Task**: Inject the `DaemonExecutorBridge` and intercept local OS calls.
*   **Details**:
    *   When a tool is flagged as `@local_primitive`, serialize its payload and pipe to the daemon bridge instead of running local python code inside the server process.

---

## 4. Phase 3: Hollowing out the OS Tools

### 📝 Task 3.1: Scrub heavy Windows imports from Tool Definitions
*   **Target Files**: `src/infrastructure/tools/os/*` (e.g. `SystemSettingsTool`, `DiagnosticsTool`)
*   **Task**: Surgically extract Windows-specific python code and relocate it to the Daemon.
*   **Details**:
    *   Delete `import pyautogui`, `import pywin32`, etc. from the top of the tool files.
    *   Replace their `async run(...)` body with a dispatch call:
        ```python
        return await self.daemon_bridge.execute("type_text", {"text": text})
        ```

---

## 5. Phase 4: GUI Surgical Strip (Visual Client Mode)

### 📝 Task 4.1: Create Lightweight Boot Sequence for GUI
*   **Target File**: `src/clients/desktop/app_controller.py`
*   **Task**: Short-circuit initialization to prevent heavy AI loading.
*   **Details**:
    *   Modify `initialize_components` to detect `uplink_enabled`.
    *   If true, **ABORT** standard `SystemFactory.build_core_engine()` instantiation entirely.
    *   Only initialize: local sound manager, local audio pipeline, visualizer window, and standard telemetry WebSocket client.
*   **Verification**: Print the process `sys.modules` during GUI startup and verify ZERO instances of `litellm`, `langgraph`, or `google.generativeai`.

---

## 6. Testing & Verification Checklist

1.  **Terminal 1**: Run `python src/remote/server.py`. Confirm Starlette boots.
2.  **Terminal 2**: Run `python src/daemons/hands.py`. Confirm successful handshake and `CHAD_SERVER_TOKEN` authenticated state.
3.  **Terminal 3**: Run `python main_gui.py`. Confirm instant render (under 1s) and audio stream start.
4.  **Action Verification**: Issue a voice command to "type 'Hello World'".
    *   Verify **GUI** captures and streams PCM.
    *   Verify **Server** processes thinking and issues JSON dispatch.
    *   Verify **Daemon** captures JSON and physically types the keys.
    *   Verify **GUI** updates with "Typing..." visual feedback.
5.  **Resiliency Dry-Run**: Force kill the GUI window mid-command. Confirm Daemon continues execution and Server returns success.
