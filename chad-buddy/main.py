"""
Chad Desktop Buddy — standalone desktop pet.

Runs independently of the main GUI / command-bar HUD. Polls the Brain
``GET /buddy/state`` endpoint for sustained modes and one-shot animation cues.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"

_webview_cache = ROOT / "data" / "webview_cache"
_webview_cache.mkdir(parents=True, exist_ok=True)
os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(_webview_cache)

import webview  # noqa: E402

PET_WIDTH = 260
PET_HEIGHT = 280
PET_SIZE = 200
MARGIN = 24
TASKBAR_RESERVE = 48
MOUSE_SLEEP_SECS = 60
TICK_INTERVAL = 0.05
EYE_MAX = 3.0
BG_COLOR = "#2b2b2b"
BRAIN_RECONNECT_SECS = 2.0
BRAIN_MODES = frozenset({"idle", "listening", "thinking", "working", "error"})


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_work_area() -> tuple[int, int, int, int]:
    rect = RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def get_cursor() -> tuple[float, float]:
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return float(pt.x), float(pt.y)


def _brain_base_url() -> str:
    raw = os.getenv("CHAD_SERVER_URL", "http://localhost:8767")
    return raw.replace("ws://", "http://").replace("wss://", "https://").rstrip("/").replace("/ws", "")


class ChadApi:
    def __init__(self, buddy: ChadBuddy) -> None:
        self._buddy = buddy

    def show_menu(self) -> None:
        self._buddy.quit()

    def quit(self) -> None:
        self._buddy.quit()


class ChadBuddy:
    def __init__(self) -> None:
        self.window: webview.Window | None = None
        self.api = ChadApi(self)
        self.state = "idle"
        self._brain_mode = "idle"
        self._local_sleeping = False
        self._running = True
        self._mouse_still_since = time.monotonic()
        self._last_cursor = (-1.0, -1.0)
        self._last_eye = (0.0, 0.0)
        self._last_cue_key: str | None = None
        self._last_comment_key: str | None = None
        self._lock = threading.Lock()

    def _bottom_right_position(self) -> tuple[int, int]:
        wx, wy, ww, wh = get_work_area()
        x = wx + ww - PET_WIDTH - MARGIN
        y = wy + wh - PET_HEIGHT - TASKBAR_RESERVE - MARGIN
        return max(wx, x), max(wy, y)

    def _display_state(self) -> str:
        with self._lock:
            if self._brain_mode in ("working", "error"):
                return self._brain_mode
            # Voice mode: wake word heard — show it even over local sleeping.
            if self._brain_mode == "listening":
                return "listening"
            if self._brain_mode == "thinking":
                return "thinking"
            if self._local_sleeping:
                return "sleeping"
            return "idle"

    def _apply_display_state(self) -> None:
        state = self._display_state()
        with self._lock:
            if self.state == state:
                return
            self.state = state
        if self.window:
            try:
                self.window.evaluate_js(f"window.chadPet.setMode({json.dumps(state)})")
            except Exception:
                pass

    def _set_brain_mode(self, mode: str) -> None:
        if mode not in BRAIN_MODES:
            return
        with self._lock:
            self._brain_mode = mode
        self._apply_display_state()

    def _play_brain_cue(self, cue: dict) -> None:
        animation = cue.get("animation")
        if not animation:
            return
        cue_key = f"{animation}:{cue.get('rule', '')}:{cue.get('until', '')}"
        with self._lock:
            if cue_key == self._last_cue_key:
                return
            self._last_cue_key = cue_key
        if self.window:
            try:
                meta = json.dumps(cue.get("meta") or {})
                self.window.evaluate_js(
                    f"window.chadPet.playCue({json.dumps(animation)}, {meta})"
                )
            except Exception:
                pass

    def _show_brain_comment(self, comment: dict) -> None:
        text = comment.get("text")
        if not text:
            return
        comment_key = f"{text}:{comment.get('until', '')}"
        with self._lock:
            if comment_key == self._last_comment_key:
                return
            self._last_comment_key = comment_key
        if self.window:
            try:
                until = float(comment.get("until", 0))
                remaining_ms = max(5000, int((until - time.monotonic()) * 1000))
                self.window.evaluate_js(
                    f"window.chadPet.showComment({json.dumps(text)}, {remaining_ms})"
                )
            except Exception:
                pass

    def _set_local_sleeping(self, sleeping: bool) -> None:
        with self._lock:
            self._local_sleeping = sleeping
        self._apply_display_state()

    def _emit_eyes(self, dx: float, dy: float) -> None:
        if self.window:
            try:
                self.window.evaluate_js(f"window.chadPet.moveEyes({dx}, {dy})")
            except Exception:
                pass

    def _on_loaded(self) -> None:
        self._inject_svgs()
        pos = self._window_position()
        print(f"[Chad Buddy] Ready at {pos or 'unknown'} — bottom-right corner", flush=True)

    def _window_position(self) -> tuple[int, int] | None:
        if not self.window:
            return None
        try:
            return self.window.x, self.window.y
        except (TypeError, AttributeError):
            return None

    def _inject_svgs(self) -> None:
        if not self.window:
            return
        for name in ("idle", "sleeping", "poke", "working"):
            path = ROOT / "assets" / "svg" / f"chad-{name}.svg"
            raw = path.read_text(encoding="utf-8")
            self.window.evaluate_js(
                f"window.chadPet.registerSvg({json.dumps(name)}, {json.dumps(raw)})"
            )
        self.window.evaluate_js("window.chadPet.setMode('idle')")

    def quit(self) -> None:
        self._running = False
        if self.window:
            self.window.destroy()

    def _tick_loop(self) -> None:
        while self._running:
            time.sleep(TICK_INTERVAL)
            if not self.window:
                continue

            cx, cy = get_cursor()
            moved = (
                abs(cx - self._last_cursor[0]) > 0.5
                or abs(cy - self._last_cursor[1]) > 0.5
            )
            if moved:
                self._last_cursor = (cx, cy)
                self._mouse_still_since = time.monotonic()

            with self._lock:
                brain = self._brain_mode
                sleeping = self._local_sleeping

            if sleeping and moved:
                self._set_local_sleeping(False)
                continue

            idle_secs = time.monotonic() - self._mouse_still_since
            if not sleeping and brain == "idle" and idle_secs >= MOUSE_SLEEP_SECS:
                self._set_local_sleeping(True)
                continue

            if self._display_state() != "idle":
                continue

            pos = self._window_position()
            if pos is None:
                continue
            win_x, win_y = pos
            center_x = win_x + PET_WIDTH / 2
            center_y = win_y + PET_HEIGHT * 0.72
            raw_dx = cx - center_x
            raw_dy = cy - center_y
            dist = max((raw_dx**2 + raw_dy**2) ** 0.5, 1.0)
            dx = max(-EYE_MAX, min(EYE_MAX, raw_dx / dist * EYE_MAX))
            dy = max(-EYE_MAX, min(EYE_MAX, raw_dy / dist * EYE_MAX))

            if abs(dx - self._last_eye[0]) > 0.1 or abs(dy - self._last_eye[1]) > 0.1:
                self._last_eye = (dx, dy)
                self._emit_eyes(dx, dy)

    def _apply_brain_payload(self, data: dict) -> None:
        mode = data.get("mode") or data.get("state", "idle")
        self._set_brain_mode(mode)
        cue = data.get("cue")
        if isinstance(cue, dict):
            self._play_brain_cue(cue)
        comment = data.get("comment")
        if isinstance(comment, dict):
            self._show_brain_comment(comment)

    def _brain_stream_loop(self) -> None:
        """Subscribe to Brain SSE — state pushed only on change."""
        url = f"{_brain_base_url()}/buddy/stream"
        while self._running:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"Accept": "text/event-stream"},
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    buffer = ""
                    while self._running:
                        chunk = resp.read(256)
                        if not chunk:
                            break
                        buffer += chunk.decode("utf-8", errors="ignore")
                        while "\n\n" in buffer:
                            block, buffer = buffer.split("\n\n", 1)
                            for line in block.splitlines():
                                if line.startswith("data: "):
                                    data = json.loads(line[6:])
                                    self._apply_brain_payload(data)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                if self._running:
                    print(f"[Chad Buddy] Brain stream lost ({exc}); reconnecting…", flush=True)
                    time.sleep(BRAIN_RECONNECT_SECS)

    def run(self) -> None:
        x, y = self._bottom_right_position()
        ui_path = UI_DIR / "index.html"
        print(f"[Chad Buddy] Launching at ({x}, {y})", flush=True)

        self.window = webview.create_window(
            "Chad Buddy",
            url=str(ui_path),
            x=x,
            y=y,
            width=PET_WIDTH,
            height=PET_HEIGHT,
            frameless=True,
            easy_drag=True,
            on_top=True,
            transparent=False,
            shadow=False,
            background_color=BG_COLOR,
            resizable=False,
            js_api=self.api,
        )
        self.window.events.loaded += self._on_loaded

        threading.Thread(target=self._tick_loop, daemon=True).start()
        threading.Thread(target=self._brain_stream_loop, daemon=True).start()

        webview.start(debug=False)


def main() -> None:
    if sys.platform != "win32":
        print("Chad Buddy is Windows-only for now.")
        sys.exit(1)
    ChadBuddy().run()


if __name__ == "__main__":
    main()
