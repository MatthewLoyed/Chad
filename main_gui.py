if __name__ == '__main__':
    # Divert frozen worker processes before importing the GUI or heavy libraries.
    import multiprocessing
    multiprocessing.freeze_support()

import os
import sys
import warnings
import asyncio

# When packaged as Chad.exe (PyInstaller), point the process at the real repo
# (CHAD_HOME) and chdir into it BEFORE any cwd/project-root-relative paths are
# computed below. No-op from a source checkout.
from src.shared.frozen_bootstrap import bootstrap_frozen, redirect_stdio_for_frozen_windowed
bootstrap_frozen()

# A frozen windowed build (console=False) has unusable stdout/stderr — print()
# crashes and logging silently drops records. Redirect to a real log file
# before anything else prints, so failures are never invisible.
_gui_log_path = redirect_stdio_for_frozen_windowed()

# Stamp which git SHA / UI bundle this GUI process loaded (deployed vs running checks).
from src.shared.runtime_identity import record_runtime_identity
record_runtime_identity("gui")

# NOTE: previously forced WEBVIEW2_DEFAULT_BACKGROUND_COLOR=0 for a transparent
# window. Removed — WebView2's transparent-background mode is unreliable and
# frequently renders solid black instead of true transparency (Microsoft
# confirms this: MicrosoftEdge/WebView2Feedback#888, #2419). The window now
# renders a normal opaque background via create_window's background_color
# param (src/clients/desktop/main_window.py), matching index.html's solid bg.

# Silencing the Windows COM noise and other verbose warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pycaw")
warnings.filterwarnings("ignore", category=UserWarning, module="comtypes")
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")

# Fix "Access is denied" cache errors by using a dedicated user data folder
# This prevents clashing with other WebView2 processes or system-protected folders.
user_data_path = os.path.join(os.getcwd(), "data", "webview_cache")
os.makedirs(user_data_path, exist_ok=True)
os.environ['WEBVIEW2_USER_DATA_FOLDER'] = user_data_path

try:
    from src.shared.benchmark import mark
    mark("Application Process Started")
except ImportError:
    mark = lambda x: None

# CRITICAL: Force Selector Loop for Windows GUI Compatibility
# This prevents the 'SystemError: new style getargs' crash in _overlapped.
from src.shared.debug_helper import debug_print

# Production loads file:// dist/ — WebView2 aggressively caches JS chunks. After an
# update rebuilds dist/ with new hashed filenames, a stale cache makes lazy imports
# (e.g. SettingsView-*.js) 404. Clear the cache when the on-disk bundle changed.
if os.environ.get("CHAD_UI_DEV", "true").lower() != "true":
    try:
        from src.shared.ui_build import invalidate_stale_webview_cache
        invalidate_stale_webview_cache()
    except Exception as e:
        debug_print(f"[ UI ] WebView cache check skipped: {e}")

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    debug_print("[ SYSTEM ] : Event Loop Policy set to Selector (Desktop Mode).")

import webview
from src.clients.desktop.app_controller import VoiceAssistantApp

def _show_fatal_error(message: str) -> None:
    """Last-resort visible failure: a native message box.

    Runs even if the webview/Tkinter stack never got far enough to render
    anything, so a crash is never silent from the user's perspective.
    """
    import traceback
    traceback.print_exc()  # goes to the redirected log file when frozen
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "Chad failed to start", 0x10)  # MB_ICONERROR
        except Exception:
            pass


def main():
    """Main entry point for the GUI application."""
    try:
        app = VoiceAssistantApp()
        # Run the application (this blocks until window is closed)
        app.run()
    except Exception as e:
        log_hint = f"\n\nLog: {_gui_log_path}" if _gui_log_path else ""
        _show_fatal_error(f"Chad crashed on startup:\n\n{e}{log_hint}")
        sys.exit(1)
    finally:
        # In dev mode, closing the GUI window cleanly stops the dev background stack (Vite + Brain)
        if os.environ.get("CHAD_UI_DEV", "").lower() == "true":
            try:
                import subprocess
                stop_script = os.path.join(os.path.dirname(__file__), "scripts", "prod", "stop_dev_stack.ps1")
                if os.path.exists(stop_script):
                    subprocess.run(
                        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", stop_script],
                        timeout=10,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            except Exception:
                pass

if __name__ == "__main__":
    main()
