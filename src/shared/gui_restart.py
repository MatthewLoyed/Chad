"""Relaunch the Chad desktop GUI after a production update."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def spawn_gui_relaunch_watchdog(parent_pid: int | None = None) -> None:
    """Start a detached helper that relaunches main_gui.py after parent_pid exits."""
    pid = parent_pid or os.getpid()
    repo = Path.cwd()
    helper = repo / "scripts" / "prod" / "relaunch_gui.py"
    if not helper.is_file():
        logger.error("[ UPDATE ] relaunch_gui.py not found at %s", helper)
        return

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )

    subprocess.Popen(
        [sys.executable, str(helper), "--wait-pid", str(pid)],
        cwd=str(repo),
        env=os.environ.copy(),
        creationflags=creationflags,
        close_fds=True,
    )
    logger.warning("[ UPDATE ] GUI relaunch watchdog started (parent PID %s)", pid)


def close_window_for_restart(main_window_ref: Callable, delay_s: float = 0.1) -> None:
    """Destroy the webview window after a short delay (watchdog must already be spawned)."""

    def _close() -> None:
        time.sleep(delay_s)
        window = main_window_ref() if callable(main_window_ref) else main_window_ref
        app = None
        if window and hasattr(window, "_app_ref"):
            app = window._app_ref()
            if app:
                app.is_running = False
                if hasattr(app, "process_guard"):
                    try:
                        app.process_guard.cleanup()
                    except Exception:
                        pass
        try:
            import webview

            if window and getattr(window, "webview_window", None):
                webview.destroy_window(window.webview_window)
                return
        except Exception as exc:
            logger.warning("[ UPDATE ] webview destroy failed, forcing exit: %s", exc)

        if window and hasattr(window, "on_close"):
            try:
                window.on_close()
                return
            except Exception:
                pass

        os._exit(0)

    threading.Thread(target=_close, name="GuiClose", daemon=True).start()


def request_gui_restart(main_window_ref: Callable) -> None:
    """Schedule a full GUI restart (used after Update Chad succeeds)."""

    def _restart() -> None:
        time.sleep(0.4)  # let the pywebview API response reach React first
        window = main_window_ref() if callable(main_window_ref) else main_window_ref
        app = None
        if window and hasattr(window, "_app_ref"):
            app = window._app_ref()
            if app:
                app.is_running = False
                if hasattr(app, "process_guard"):
                    try:
                        app.process_guard.cleanup()
                    except Exception:
                        pass

        try:
            import webview

            if window and getattr(window, "webview_window", None):
                webview.destroy_window(window.webview_window)
                return
        except Exception as exc:
            logger.warning("[ UPDATE ] webview destroy failed, forcing exit: %s", exc)

        if window and hasattr(window, "on_close"):
            try:
                window.on_close()
                return
            except Exception:
                pass

        os._exit(0)

    spawn_gui_relaunch_watchdog()
    threading.Thread(target=_restart, name="GuiRestart", daemon=True).start()
