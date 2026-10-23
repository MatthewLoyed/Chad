"""
👐 The Hands Daemon (OS Automation Daemon)
Invisible background Windows runner that executes OS-level primitives.
Acts as the physical "Hands" for The Brain (Server).
"""

import os
import sys
import json
import time
import asyncio
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional, Tuple

# Ensure dependencies are available
try:
    import websockets
except ImportError:
    print("[ FATAL ] : 'websockets' library not installed. Daemon cannot run.")
    sys.exit(1)

from src.daemons.protocol import DaemonAction, DaemonRequest, DaemonResponse

# Local OS Libraries (Imported dynamically inside thread pool to avoid blocking import delays)
# We define helper executor handlers to keep core loop light.

def _resolve_daemon_url() -> str:
    """Daemon endpoint, derived from CHAD_SERVER_URL - the same env var the GUI/Eyes
    client uses to pick a Brain (see src/remote/limb.py:_resolve_server_url). Without
    this, a dev stack on a non-default port (e.g. 8788) silently gets a Hands Daemon
    that connects to prod's 8767 instead, so dev never sees a daemon register."""
    base = os.getenv("CHAD_SERVER_URL")
    if base and base.strip():
        base = base.strip()
        # CHAD_SERVER_URL is typically ".../ws" (the GUI/Eyes path) - swap for the daemon path.
        if base.endswith("/ws"):
            base = base[: -len("/ws")]
        else:
            base = base.rstrip("/")
        return f"{base}/ws/daemon"
    return "ws://localhost:8767/ws/daemon"

def _profile_auth_token():
    """Read first-run credentials after the Brain has generated them on disk."""
    from src.shared.config_manager import ConfigManager
    ConfigManager.load_config()
    return ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")


class HandsDaemon:
    def __init__(self, server_url: Optional[str] = None):
        self.server_url = server_url or _resolve_daemon_url()
        self.auth_token = os.getenv("CHAD_SERVER_TOKEN")
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._running = True
        
        print(f"[+] Hands Daemon Initialized. Connecting to: {self.server_url}")

    async def start(self):
        """Execute the client connection retry loop."""
        retry_delay = 1.0
        while self._running:
            try:
                print(f"[ CONNECTING ] : Attempting handshake at {self.server_url}")
                headers = {"Origin": "http://localhost:5173"}
                connect_kwargs = {
                    "ping_interval": 20,
                    "ping_timeout": 60
                }
                
                try:
                    async with websockets.connect(self.server_url, additional_headers=headers, **connect_kwargs) as ws:
                        retry_delay = 1.0  # Reset backoff on success
                        await self._run_daemon_loop(ws)
                except TypeError as e:
                    if "additional_headers" in str(e):
                        async with websockets.connect(self.server_url, extra_headers=headers, **connect_kwargs) as ws:
                            retry_delay = 1.0  # Reset backoff on success
                            await self._run_daemon_loop(ws)
                    else:
                        raise e
            except websockets.exceptions.ConnectionClosed:
                print("[ DISCONNECTED ] : Pipe severed by Server.")
            except Exception as e:
                print(f"[ NETWORK ERROR ] : Failed connecting. {e}")
                
            # Exponential backoff for connection recovery
            print(f"[ RETRY ] : Waiting {retry_delay:.1f}s before reconnect...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)

    async def _run_daemon_loop(self, ws):
        print("[ CONNECTED ] : WebSocket channel established. Elevating privileges...")
        
        # 1. Perform Authentication Handshake
        await ws.send(json.dumps({
            "type": "auth",
            "auth_token": self.auth_token or _profile_auth_token()
        }))
        
        auth_resp_raw = await ws.recv()
        auth_resp = json.loads(auth_resp_raw)
        
        if auth_resp.get("status") != "authenticated":
            print(f"[ AUTH FAIL ] : Server rejected credentials: {auth_resp.get('message')}")
            await asyncio.sleep(5)
            return
            
        print("[ SUCCESS ] : Hands Daemon safely authenticated with Brain! Listening for tasks...")
        
        # 2. Event Dispatcher Loop
        async for message_str in ws:
            try:
                raw_data = json.loads(message_str)
                # Check for standard heartbeat pings
                if raw_data.get("action") == DaemonAction.PING:
                    await ws.send(json.dumps({"request_id": raw_data.get("request_id"), "success": True, "result": "pong"}))
                    continue
                    
                req = DaemonRequest.from_dict(raw_data)
                print(f"[ TASK ] : Received request_id={req.request_id[:8]}, Action={req.action}")
                
                # Execute in separate thread to protect event loop latency
                asyncio.create_task(self._dispatch_and_respond(ws, req))
                
            except Exception as e:
                print(f"[ FAIL ] : Dropped frame: {e}")

    async def _dispatch_and_respond(self, ws, request: DaemonRequest):
        """Executes the synchronous Windows command in an executor and replies."""
        try:
            # Call execution via thread pool to avoid locking WebSocket heartbeat
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(self._executor, self._execute_primitive, request.action, request.payload)
            
            resp = DaemonResponse(
                request_id=request.request_id,
                success=True,
                result=result
            )
        except Exception as e:
            print(f"[ ERROR ] : Task {request.request_id[:8]} crashed: {e}")
            resp = DaemonResponse(
                request_id=request.request_id,
                success=False,
                error=str(e)
            )
            
        try:
            await ws.send(resp.to_json())
        except Exception as e:
            print(f"[ SHIT ] : Failed to send response back to WebSocket: {e}")

    def _resolve_point(self, x: Any, y: Any, target_window: Optional[str] = None) -> Tuple[int, int]:
        """
        Resolve a click/move target to absolute, on-screen desktop coordinates.

        Coordinate safety:
          - If `target_window` is given, (x, y) are treated as window-relative and
            offset by the window's top-left rect (reusing CAPTURE_SCREEN's win32gui logic).
          - The resolved point is clamped to the virtual screen bounds so a stray
            coordinate can never fling the cursor off-screen or onto another monitor.
          - Non-numeric / missing coords are rejected loudly.
        """
        try:
            px, py = int(round(float(x))), int(round(float(y)))
        except (TypeError, ValueError):
            raise ValueError(f"Invalid coordinates: x={x!r}, y={y!r} (must be numbers).")

        # Window-relative → absolute offset
        if target_window:
            try:
                import win32gui

                matches = []

                def enum_cb(hwnd, _):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        if title and target_window.lower() in title.lower():
                            matches.append((hwnd, title))
                    return True

                win32gui.EnumWindows(enum_cb, None)
                if not matches:
                    raise RuntimeError(f"Target window '{target_window}' not found.")
                matches.sort(key=lambda m: len(m[1]))
                hwnd, matched_title = matches[0]
                try:
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.15)
                except Exception:
                    pass
                left, top, _, _ = win32gui.GetWindowRect(hwnd)
                px, py = px + left, py + top
            except ImportError:
                raise RuntimeError("win32gui unavailable; cannot resolve window-relative coordinates.")

        # Clamp to the full virtual desktop (all monitors)
        try:
            import win32api
            import win32con

            vx = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            vy = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
            vw = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            vh = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            min_x, min_y = vx, vy
            max_x, max_y = vx + vw - 1, vy + vh - 1
        except Exception:
            # Fallback to primary screen size via pynput/OS
            min_x = min_y = 0
            max_x = max_y = 1_000_000  # effectively unclamped if metrics unavailable

        clamped_x = max(min_x, min(px, max_x))
        clamped_y = max(min_y, min(py, max_y))
        return clamped_x, clamped_y

    def _execute_primitive(self, action: str, payload: Dict[str, Any]) -> Any:
        """Synchronous CPU-bound Windows execution routines (Executed inside threadpool)."""

        if action in (
            DaemonAction.CLICK,
            DaemonAction.RIGHT_CLICK,
            DaemonAction.DOUBLE_CLICK,
            DaemonAction.MOVE,
        ):
            from pynput.mouse import Controller, Button

            target_window = payload.get("target_window")
            x, y = self._resolve_point(payload.get("x"), payload.get("y"), target_window)

            mouse = Controller()
            mouse.position = (x, y)
            time.sleep(0.03)  # let the OS register the move before pressing

            if action == DaemonAction.MOVE:
                return f"Moved cursor to ({x}, {y})."
            if action == DaemonAction.RIGHT_CLICK:
                mouse.click(Button.right, 1)
                return f"Right-clicked at ({x}, {y})."
            if action == DaemonAction.DOUBLE_CLICK:
                mouse.click(Button.left, 2)
                return f"Double-clicked at ({x}, {y})."
            # Default: single left click
            mouse.click(Button.left, 1)
            return f"Clicked at ({x}, {y})."

        elif action == DaemonAction.DRAG:
            from pynput.mouse import Controller, Button

            target_window = payload.get("target_window")
            sx, sy = self._resolve_point(payload.get("from_x"), payload.get("from_y"), target_window)
            ex, ey = self._resolve_point(payload.get("to_x"), payload.get("to_y"), target_window)

            mouse = Controller()
            mouse.position = (sx, sy)
            time.sleep(0.05)
            mouse.press(Button.left)
            time.sleep(0.05)
            mouse.position = (ex, ey)
            time.sleep(0.05)
            mouse.release(Button.left)
            return f"Dragged from ({sx}, {sy}) to ({ex}, {ey})."

        elif action == DaemonAction.TYPE_TEXT:
            text = payload.get("text", "")
            from pynput.keyboard import Controller
            keyboard = Controller()
            keyboard.type(text)
            return f"Typed {len(text)} chars."
            
        elif action == DaemonAction.SET_VOLUME:
            level = max(0, min(100, int(payload.get("level", 50))))
            import pythoncom
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            
            pythoncom.CoInitialize()
            try:
                devices = AudioUtilities.GetSpeakers()
                if not devices:
                    raise RuntimeError("No speakers detected.")
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = interface.QueryInterface(IAudioEndpointVolume)
                volume.SetMasterVolumeLevelScalar(level / 100.0, None)
                return f"System volume set to {level}%."
            finally:
                pythoncom.CoUninitialize()
                
        elif action == DaemonAction.SET_BRIGHTNESS:
            level = max(0, min(100, int(payload.get("level", 50))))
            cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {level})"
            res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Brightness failed: {res.stderr}")
            return f"Brightness set to {level}%."

        elif action == DaemonAction.CAPTURE_SCREEN:
            import io
            import time
            import base64
            from PIL import ImageGrab, Image
            
            target_window = payload.get("target_window")
            all_screens = bool(payload.get("all_screens", False))
            left, top, right, bottom = None, None, None, None
            capture_source = "full screen"

            # Windows Enumeration block
            if target_window:
                try:
                    import win32gui
                    import win32con
                    
                    matches = []
                    def enum_cb(hwnd, _):
                        if win32gui.IsWindowVisible(hwnd):
                            title = win32gui.GetWindowText(hwnd)
                            if title and target_window.lower() in title.lower():
                                matches.append((hwnd, title))
                        return True
                    
                    win32gui.EnumWindows(enum_cb, None)
                    if matches:
                        matches.sort(key=lambda x: len(x[1]))
                        hwnd, matched_title = matches[0]
                        try:
                            win32gui.SetForegroundWindow(hwnd)
                            time.sleep(0.15)
                        except:
                            pass
                        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                        capture_source = f"window '{matched_title}'"
                except Exception as win_err:
                    print(f"[ WARN ] Window enumeration failed: {win_err}")

            # Capture
            if left is not None and right > left and bottom > top:
                screenshot = ImageGrab.grab(bbox=(left, top, right, bottom))
            else:
                screenshot = ImageGrab.grab(all_screens=all_screens)
                capture_source = "all screens" if all_screens else "full screen"

            # Downscale constraint to prevent network bloat
            MAX_DIM = 1024
            w, h = screenshot.size
            if w > MAX_DIM or h > MAX_DIM:
                ratio = min(MAX_DIM / w, MAX_DIM / h)
                screenshot = screenshot.resize((int(w*ratio), int(h*ratio)), Image.Resampling.LANCZOS)

            # Serialize
            buf = io.BytesIO()
            screenshot.convert('RGB').save(buf, format='JPEG', quality=85)
            # Return Base64 string because JSON cannot hold raw bytes
            b64_str = base64.b64encode(buf.getvalue()).decode('utf-8')
            
            return {
                "success": True,
                "source": capture_source,
                "image_b64": b64_str,
                "width": screenshot.width,
                "height": screenshot.height
            }

        elif action == DaemonAction.RESIZE_WINDOW:
            import pygetwindow as gw
            title = payload.get("window_title")
            w, h = payload.get("width"), payload.get("height")
            
            windows = gw.getWindowsWithTitle(title)
            if not windows:
                raise RuntimeError(f"Window '{title}' not found.")
            win = windows[0]
            if win.isMinimized: win.restore()
            if w and h:
                win.resizeTo(w, h)
            return f"Resized '{win.title}'."

        elif action == DaemonAction.SWITCH_WINDOW:
            import pygetwindow as gw
            title = payload.get("window_title")
            windows = gw.getWindowsWithTitle(title)
            if not windows:
                raise RuntimeError(f"Window '{title}' not found.")
            win = windows[0]
            if win.isMinimized: win.restore()
            win.activate()
            return f"Switched focus to '{win.title}'."

        elif action == DaemonAction.MOVE_WINDOW:
            # Explicit x/y only. Monitor-targeted moves go through the windows-semantic-ui
            # MCP server's window_management(action='move_to_monitor') instead — it pairs
            # the move with a list_monitors discovery call (real x/y/width/height/isPrimary),
            # so the caller can verify which index/target is which before acting. This tool
            # used to guess a monitor_index against its own left-to-right sort with no
            # discovery step at all, which silently disagreed with windows-semantic-ui's own
            # (different) monitor numbering and sent windows to the wrong screen.
            import pygetwindow as gw
            title = payload.get("window_title")
            windows = gw.getWindowsWithTitle(title)
            if not windows:
                raise RuntimeError(f"Window '{title}' not found.")
            win = windows[0]
            if win.isMinimized: win.restore()

            x, y = payload.get("x"), payload.get("y")
            if x is not None and y is not None:
                win.moveTo(int(x), int(y))
                return f"Moved '{win.title}' to ({x}, {y})."

            raise ValueError(
                "move_window requires explicit x/y coordinates. For monitor-targeted moves, "
                "use window_management(action='move_to_monitor') instead."
            )

        elif action == DaemonAction.SCROLL:
            direction = payload.get("direction", "down")
            amount = int(payload.get("amount", 3))
            
            # Fast scroll via PowerShell COM SendKeys
            key = "{PGDN}" if direction == "down" else "{PGUP}"
            ps_script = ["$wshell = New-Object -ComObject WScript.Shell;"]
            for _ in range(amount):
                ps_script.append(f"$wshell.SendKeys('{key}');")
                ps_script.append("Start-Sleep -m 50;")
            
            subprocess.run(["powershell", "-NoProfile", "-Command", "; ".join(ps_script)], capture_output=True)
            return f"Scrolled {direction} {amount} times."

        elif action == DaemonAction.CHECK_APP_EXISTS:
            from src.daemons.app_lookup import check_application
            return check_application(payload.get("name", ""), payload.get("explicit_path"))

        elif action == DaemonAction.LAUNCH_APP:
            import shutil
            import platform
            import webbrowser
            
            from src.daemons.app_lookup import resolve_application
            match = resolve_application(payload.get("name", ""), payload.get("explicit_path"))
            target_path, method = match["path"], match["method"]
            url = payload.get("url")
            args = payload.get("args") or []

            # --- EXECUTION SEQUENCE ---
            exec_args = list(args)
            if url: exec_args.append(url)
            
            if method == "url":
                webbrowser.open(target_path)
            elif method == "windows_uri" or target_path.lower().endswith(".lnk"):
                os.startfile(target_path)
            else:
                cmd = [target_path] + exec_args
                subprocess.Popen(cmd, start_new_session=True, creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0)
                
            return f"Successfully launched '{payload.get('name')}' using {method} resolver."

        raise ValueError(f"Unsupported Daemon action: {action}")

if __name__ == "__main__":
    # Auto-load local .env file if present to capture CHAD_SERVER_TOKEN
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    from src.core.utils.system_utils import ProcessGuard
    guard = ProcessGuard("hands_daemon.lock")
    guard.enforce_singleton()

    daemon = HandsDaemon()
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        print("\n[+] Daemon cleanly shutdown by user.")
    finally:
        guard.cleanup()
