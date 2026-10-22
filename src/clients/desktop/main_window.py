"""
Main Window for Chad Voice Assistant
Modern, clean interface with dark theme and animations.
Uses HTML/CSS/JS from index.html for rendering.
"""

import tkinter as tk
import customtkinter as ctk
import asyncio
import json
import os
import threading
import traceback
import time
from typing import Any, Dict, List, Optional, Callable
from pathlib import Path
from datetime import datetime
from src.clients.desktop.styles import COLORS, FONTS, DIMENSIONS
from src.shared.config_manager import ConfigManager
import weakref
from src.clients.desktop.api_bridge import WebViewAPI
from src.clients.desktop.components.dummy_visualizer import DummyVisualizer
from src.shared.logger import setup_logger
from src.shared.frozen_bootstrap import is_frozen

logger = setup_logger("MainWindow")


# webview import deferred to __init__
WEBVIEW_AVAILABLE = None


def _current_git_branch() -> Optional[str]:
    """Best-effort current branch name; ``None`` if git/repo isn't available."""
    import shutil
    import subprocess

    git = shutil.which("git")
    if not git:
        return None
    try:
        result = subprocess.run(
            [git, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _desktop_ui_url(url: str) -> str:
    """Identify the desktop before asynchronous native-bridge injection."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parsed = urlsplit(url)
    # WebView2 treats a query on a local file URL as part of the filename.
    # The frontend already identifies file: pages as native desktop pages.
    if parsed.scheme == 'file':
        return url
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query['chadDesktop'] = '1'
    return urlunsplit(parsed._replace(query=urlencode(query)))


def _window_title() -> str:
    """Window title. Prod shows plain "Chad"; the dev stack shows a [DEV] tag
    plus the branch it is running.

    Keyed on the ``CHAD_DEV_WORKTREE`` env var that ``launch_dev.ps1`` sets for
    the dev GUI (prod never sets it), so this stays correct after the branch is
    merged into master — a prod window will never show [DEV]. Prevents the
    dev and prod windows from being indistinguishable in the taskbar.
    """
    base = "Chad - AI Assistant"
    if not os.environ.get("CHAD_DEV_WORKTREE"):
        return base
    branch = _current_git_branch()
    return f"Chad [DEV] - {branch}" if branch else "Chad [DEV]"


def _wait_for_dev_server(url: str, timeout_sec: float = 45.0) -> bool:
    """Poll the Vite dev server until it answers or ``timeout_sec`` elapses.

    launch_dev.ps1 starts the GUI as soon as its own health-check passes (or
    times out — that check has its own fallback for session-isolation cases
    where a listening port isn't reachable from the launcher's process), which
    can race a cold `npm run dev` (dependency pre-bundling on first run).
    Polling here — from the process that will actually render the page —
    avoids ever showing a bare "connection refused" WebView2 error page.
    """
    import urllib.request

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _load_timeout_error_html(attempted_url: str) -> str:
    """Visible fallback page for when the webview never fires 'loaded'."""
    from src.shared.frozen_bootstrap import is_frozen
    log_hint = ""
    if is_frozen():
        try:
            from src.shared.paths import get_log_dir
            log_hint = f"<p>Log file: {get_log_dir() / 'gui_stdio.log'}</p>"
        except Exception:
            pass
    return f"""
    <html><body style="background:#0f1115;color:#e8eaed;font-family:sans-serif;
    padding:2rem;">
    <h2>Chad's interface failed to load</h2>
    <p>Tried to load: {attempted_url}</p>
    {log_hint}
    <p>This is a bug — please report it with the log file above.</p>
    </body></html>
    """


def resolve_ui_dev_mode() -> bool:
    """Whether to load the Vite dev server (True) or the built dist/ (False).

    A frozen ``Chad.exe`` never has a dev server to reach, so it always loads
    dist/ regardless of env var. Running from source, an explicit
    ``CHAD_UI_DEV`` wins; otherwise only the ``dev`` branch defaults to the
    dev server — every other branch (master, feature branches, worktrees)
    loads dist/ like a real install would.
    """
    if is_frozen():
        return False
    env_val = os.environ.get("CHAD_UI_DEV")
    if env_val is not None:
        return env_val.strip().lower() == "true"
    return _current_git_branch() == "dev"


class MainWindow(ctk.CTk):
    """
    Main GUI Window application.
    Uses HTML/CSS/JS from index.html for the UI.
    """
    
    def __init__(self, loop=None, app_reference=None):
        super().__init__()
        
        self.loop = loop
        self.app_reference = app_reference  # Reference to VoiceAssistantApp for API access
        
        # Lazy load webview
        global WEBVIEW_AVAILABLE
        if WEBVIEW_AVAILABLE is None:
            try:
                import webview
                WEBVIEW_AVAILABLE = True
            except ImportError:
                WEBVIEW_AVAILABLE = False
                print("Warning: webview not available. Install with: pip install pywebview")
        
        # Window Setup
        self.title(_window_title())
        self.geometry(f"{DIMENSIONS['window_width']}x{DIMENSIONS['window_height']}")
        self.minsize(DIMENSIONS['min_width'], DIMENSIONS['min_height'])
        
        # Theme Setup
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.configure(fg_color=COLORS['bg_primary'])
        
        # Data storage for HTML UI
        self.action_history_data = []  # List of {command, response, sources, timestamp}
        self.live_logs_data = []  # List of {message, level, timestamp}
        self.current_caption_text = ""  # Current caption text for AI responses (final speech)
        self.current_thinking_text = "System Initialized."  # Current thinking/reasoning text for AI
        self.current_status_text = "Say \"Hey Chad\""  # Status text for voice HUD
        self.current_audio_level = 0.0  # Current audio level (0.0 to 1.0) for volume visualization
        self.is_in_active_conversation = False  # Track if we're in an active conversation
        self._current_state_enum = "idle"  # Explicit state enum for frontend (idle, listening, processing, speaking, initializing, ready, active, executing, error)
        
        # WebView for HTML rendering
        self.webview_window = None
        self.html_file_path = None
        self._webview_ready = False  # Flag to prevent blocking evaluate_js calls before webview.start()
        self._shutdown = False  # Flag to prevent evaluate_js calls after window is destroyed
        
        self._thinking_event_sequence = 0

        # Last voice-mode availability payload, replayed on webview load (see on_loaded).
        self.last_voice_availability = None

        self._last_status_push_time = 0
        self._status_throttle_delay = 0.050    # Reduced to 50ms for smoothness
        self._pending_status_text = None
        
        # State variables (preserved from original for compatibility)
        self.visualizer = DummyVisualizer(weakref.ref(self))

        self.action_history = None  # Keep for compatibility (will be HTML-based)
        self.live_logs = None  # Keep for compatibility (will be HTML-based)
        self.status_text = None  # Keep for compatibility
        self.tool_badge_container = None
        self.current_tool_badge = None
        self.transcription_display = None
        self.timeout_progress = None
        
        # Setup UI - create HTML file and webview
        self._setup_ui()
        
        # Bind close event
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Keyboard Shortcuts
        self.bind("<space>", self.on_space_press)
        self.bind("<Escape>", lambda e: self.on_close())
        
        # Note: webview.start() must run on main thread and blocks
        # We'll initialize webview synchronously so it's ready when run() is called
        # The Tkinter window will be hidden since we're using webview for UI
        if WEBVIEW_AVAILABLE:
            self.withdraw()  # Hide the Tkinter window initially
            # Initialize webview immediately (don't wait for mainloop)
            self._init_webview()
        else:
            # Fallback: create a simple placeholder if webview not available
            self._create_fallback_ui()
        
    def _setup_ui(self):
        """Setup UI - load React app (dev server or production build)."""
        # ================================================================
        # REACT MIGRATION: Toggle between dev server and production build
        # Set CHAD_UI_DEV=true in .env to use Vite dev server (hot reload)
        # Set CHAD_UI_DEV=false in .env to use production build from dist/
        # A frozen Chad.exe always uses dist/; an unset env var otherwise only
        # defaults to the dev server on the `dev` branch (see resolve_ui_dev_mode).
        # ================================================================
        DEV_MODE = resolve_ui_dev_mode()
        
        if DEV_MODE:
            # React Dev Server (requires `npm run dev` in frontend/)
            self.html_file_path = "http://localhost:5173"
            print("[ WEBVIEW ] DEV_MODE: Loading React from http://localhost:5173")
        else:
            # Production Build (requires `npm run build` in src/clients/web/).
            # In a frozen Chad.exe the build is bundled inside the app, so prefer
            # that; fall back to the source-tree dist for normal checkouts.
            from src.shared.frozen_bootstrap import bundled_web_dist
            dist_path = bundled_web_dist() or (Path(__file__).parent.parent / "web" / "dist" / "index.html")
            if dist_path.exists():
                self.html_file_path = dist_path
                print(f"[ WEBVIEW ] PROD_MODE: Loading from {dist_path}")
            else:
                # Fallback to legacy index.html if build doesn't exist
                self.html_file_path = Path(__file__).parent.parent / "web" / "index.html"
                print(f"[ WEBVIEW ] FALLBACK: No dist/ found, loading legacy index.html")
        
        if isinstance(self.html_file_path, Path) and not self.html_file_path.exists():
            print("Error: HTML file not found")
    
    
    def _init_webview(self):
        """Initialize webview window."""
        
        # Handle both URL strings (dev mode) and Path objects (production)
        if isinstance(self.html_file_path, str):
            # DEV MODE: html_file_path is a URL string like "http://localhost:5173"
            html_url = self.html_file_path
            print(f"[ WEBVIEW ] Waiting for dev server at {html_url} ...")
            if not _wait_for_dev_server(html_url):
                print(f"[ WEBVIEW ] Dev server never came up at {html_url} — loading anyway")
        elif isinstance(self.html_file_path, Path):
            # PRODUCTION MODE: html_file_path is a Path object
            if not self.html_file_path.exists():
                print("Error: HTML file not found")
                self.deiconify()  # Show Tkinter window if webview fails
                return
            html_url = f"file:///{self.html_file_path.absolute().as_posix()}"
        else:
            print("Error: Invalid html_file_path type")
            self.deiconify()
            return
        
        try:
            # Create API class for JavaScript-Python communication
            # Use weakref to avoid circular references and serialization issues
            import weakref
            window_ref = weakref.ref(self)
            api_instance = WebViewAPI(window_ref)
            self.webview_api = api_instance

            
            # Create webview window with API
            import webview # Ensure imported
            self.webview_window = webview.create_window(
                _window_title(),
                _desktop_ui_url(html_url),
                width=DIMENSIONS['window_width'],
                height=DIMENSIONS['window_height'],
                resizable=True,
                fullscreen=False,
                min_size=(DIMENSIONS['min_width'], DIMENSIONS['min_height']),
                background_color='#0f1115',
                js_api=api_instance
            )
            
            # Set up loaded event handler to mark webview as ready
            def on_loaded():
                self._webview_ready = True
                print("[ WEBVIEW ] WebView loaded - JS push methods now active")
                # Voice mode arms during startup, usually before the page loads,
                # so its one-shot availability push would otherwise be dropped and
                # the mic indicator would sit in "unknown" forever.
                if self.last_voice_availability is not None:
                    self.push_voice_availability(self.last_voice_availability)

            self.webview_window.events.loaded += on_loaded

            # webview.start() takes over the main thread (Tkinter's mainloop
            # never runs alongside it in the normal path — see
            # AppController.run()), so a load failure that never raises a
            # Python exception (e.g. the page silently fails to render) would
            # otherwise leave a black window forever with no visible cause.
            # Replace it with a visible error page instead of staying silent.
            import threading

            def _check_load_timeout():
                if self._webview_ready or self._shutdown:
                    return
                print("[ WEBVIEW ] Load timed out — page never fired 'loaded'")
                try:
                    self.webview_window.load_html(_load_timeout_error_html(html_url))
                except Exception as html_err:
                    print(f"[ WEBVIEW ] Failed to show load-timeout error page: {html_err}")

            threading.Timer(12.0, _check_load_timeout).start()

            # Store window reference for later use
            # Note: webview.start() must be called from main thread
            # It will be started in main_gui.py's run() method

        except Exception as e:
            
            print(f"Error initializing webview: {e}")
            import traceback
            traceback.print_exc()
            self.deiconify()  # Show Tkinter window if webview fails
    
    def _create_fallback_ui(self):
        """Create fallback UI if webview is not available."""
        label = ctk.CTkLabel(
            self,
            text="WebView not available. Please install pywebview:\npip install pywebview",
            font=FONTS['primary'],
            text_color=COLORS['error']
        )
        label.pack(expand=True)
    
    def _execute_js(self, script: str):
        """Execute JavaScript in webview."""
        # Note: We're using API polling instead of direct JS evaluation
        # JavaScript polls window.pywebview.api for updates, so no need to execute JS directly
        pass
    
    def _update_webview_data(self, method: str, *args):
        """Update webview with new data."""
        # Note: Updates are handled via API polling in JavaScript
        # The data is stored in self.action_history_data, self.live_logs_data, etc.
        # JavaScript polls the API every 100ms and detects changes automatically
        # These method calls are kept for compatibility but don't need to do anything
        pass
    
    def on_space_press(self, event):
        """Handle manual activation via space bar."""
        print("Manual activation requested")
    
    # --- UI Update Methods (Preserved from original, adapted for HTML) ---
    
    def update_status(self, text=None, state="idle"):
        """
        Central status update function - manages system state display.
        
        Args:
            text: Optional status text (if None, uses default for state)
            state: System state (initializing, ready, listening, active, processing, speaking, idle, error)
        """
        # Store explicit state enum for frontend
        self._current_state_enum = state
        
        # Track if we're in an active conversation (Gemini Live session)
        # These states imply the session is open and active
        if state in ["active", "listening", "speaking", "thinking", "processing", "executing", "model_loading"]:
            self.is_in_active_conversation = True
        elif state in ["ready", "idle", "error", "initializing", "reconnecting"]:
            self.is_in_active_conversation = False
        
        # Determine status text to display in the pill
        if text:
            # 1. Use explicit text if provided (errors, tool actions, etc.)
            status_text = text
        else:
            # 2. State-based defaults
            if state == "initializing":
                status_text = "Connecting..."
            elif state == "reconnecting":
                status_text = "Reconnecting..."
            elif state == "ready":
                # ONLY state where wake word is actively being monitored
                status_text = 'Say "Hey Chad"'
            elif state == "active":
                status_text = "Uplink Active"
            elif state == "listening":
                status_text = "Listening..."
            elif state == "speaking":
                status_text = "Speaking..."
            elif state in ["thinking", "processing"]:
                status_text = "Thinking..."
            elif state == "executing":
                status_text = "Executing Action..."
            elif state == "error":
                status_text = "ERROR"
            elif state == "model_loading":
                status_text = "Loading Model..."
            else:
                # idle or unknown: System is in standby/not listening for wake word
                status_text = "[ STANDBY ]"
        
        self.current_status_text = status_text
        
        # Push to JS
        self.push_status_text(status_text)
        if state:
            self.push_state(state)
    
    def update_transcript(self, text: str, is_partial: bool = False):
        """Update transcription display."""
        # For user transcripts, we can show in status or log
        if not is_partial and text:
            self.add_log(f"User: {text}", "INFO")
    
    def update_caption(self, text: str, use_scramble: bool = True):
        """
        Update caption text with TextScramble effect.
        This is called when the AI speaks (model transcript).
        
        Args:
            text: The text to display in the caption area
            use_scramble: Whether to use TextScramble effect (True for final, False for partial/streaming)
        """
        if text is not None:
            self.current_caption_text = text
            # Always use scramble effect for now (TextScramble handles it gracefully)
            self._update_webview_data('caption', text)
    
    def show_tool_badge(self, tool_name: str):
        """Show tool badge when tool execution starts."""
        # Tool badges shown via status updates
        pass
    
    def hide_tool_badge(self):
        """Hide tool badge when tool execution completes."""
        pass
    
    def add_action_history(self, command: str, response: str, sources=None):
        """
        Add entry to action history.
        
        Args:
            command: User command text
            response: Sudo's response/action text
            sources: Optional list of source dicts with 'url' and 'title' keys
        """
        entry = {
            'command': command,
            'response': response,
            'sources': sources or [],
            'timestamp': datetime.now().strftime("%H:%M")
        }
        self.action_history_data.append(entry)
        
        # Keep only last 30 entries
        if len(self.action_history_data) > 30:
            self.action_history_data = self.action_history_data[-30:]
        
        # self._update_webview_data('actionHistory') # Polling legacy
        self.push_action(command, response) # Push new item directly
    
    def update_most_recent_entry_sources(self, sources):
        """
        Update the most recent action history entry with sources.
        
        Args:
            sources: List of source dicts with 'url' and 'title' keys
        """
        if self.action_history_data:
            self.action_history_data[-1]['sources'] = sources
            self._update_webview_data('actionHistory')
    
    def add_log(self, message: str, level: str = "INFO"):
        """Add log message to live logs widget."""
        # Normalize log level: handle both string and integer levels
        # Map standard integer levels to strings
        level_map = {40: 'ERROR', 30: 'WARNING', 20: 'INFO', 10: 'DEBUG'}
        
        # Convert level to uppercase string
        if isinstance(level, int):
            level_str = level_map.get(level, str(level)).upper()
        else:
            level_str = str(level).upper()
        
        entry = {
            'message': message,
            'level': level_str,
            'timestamp': datetime.now().strftime("%H:%M:%S")
        }
        self.live_logs_data.append(entry)
        
        # Keep only last 1000 entries
        if len(self.live_logs_data) > 1000:
            self.live_logs_data = self.live_logs_data[-1000:]
        
        self._update_webview_data('liveLogs')
    
    def set_system_text(self, text, mode="response"):
        """
        Legacy method - kept for compatibility.
        Updates caption with AI response text.
        """
        if mode == "response" and text:
            self.update_caption(text)
    
    def set_thinking_text(self, text, append=False):
        """Display AI thinking text in the thinking box."""
        if text:
            if append:
                self.current_thinking_text += text
            else:
                self.current_thinking_text = text
        else:
            if not append:
                self.current_thinking_text = ""
    
    def set_reasoning_text(self, text, append=False):
        """Display AI reasoning text in the thinking box (same as thinking)."""
        if text:
            if append:
                self.current_thinking_text += text
            else:
                self.current_thinking_text = text
        else:
            if not append:
                self.current_thinking_text = ""
    
    def set_action_text(self, text):
        """Display action status (e.g., 'Opening YouTube...')."""
        if text:
            self.update_status(text=text, state="executing")
    
    # ================================================================
    # EVENT-DRIVEN PUSH METHODS (Production)
    # ================================================================

    def _safe_json_dumps(self, data: Any) -> str:
        """Safe JSON serialization that handles bytes and other objects."""
        def default_encoder(obj):
            if isinstance(obj, bytes):
                return f"<binary data: {len(obj)} bytes>"
            return str(obj)
        return json.dumps(data, default=default_encoder)

    def push_state(self, state: str):
        """
        Push state update to JavaScript.
        Values: idle, listening, thinking, speaking
        """
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            # Fire and forget - don't wait for result
            try:
                self.webview_window.evaluate_js(f'window.handleStateUpdate({json.dumps(state)})')
            except Exception:
                pass

    def push_status_text(self, text: str):
        """Push custom status text (e.g. 'Opening YouTube...') to JavaScript with throttling."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
            
        self._pending_status_text = text
        now = time.time()
        
        # If it's an error or state change (e.g. 'ERROR'), push immediately
        if "ERROR" in text.upper() or len(text) < 5 or now - self._last_status_push_time > self._status_throttle_delay:
            self._flush_status_text()

    def _flush_status_text(self):
        """Force push the latest buffered status text."""
        if not self._pending_status_text or self._shutdown or not self.webview_window:
            return
            
        try:
            self.webview_window.evaluate_js(f'window.handleStatusText({json.dumps(self._pending_status_text)})')
            self._last_status_push_time = time.time()
            self._pending_status_text = None
        except Exception:
            pass

    def push_caption(self, text: str, append: bool = False):
        """Push caption update to JavaScript.

        The answer text now streams through the typed path (push_stream_event →
        window.applyStreamEvent). This legacy caption sink is kept but typeof-guarded
        so it is a safe no-op when no window.handleCaptionUpdate handler is installed.
        """
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return

        self._flush_status_text()

        try:
            self.webview_window.evaluate_js(
                f'(window.handleCaptionUpdate && window.handleCaptionUpdate({json.dumps(text)}, {str(append).lower()}))'
            )
        except Exception:
            pass

    def push_voice_state(self, payload: dict):
        """Push a voice-turn phase (see src.shared.voice_state.VoicePhase) to JS.

        Deliberately a separate channel from push_state/push_status_text: those are
        shared with the chat turn lifecycle and get overwritten by unrelated
        activity, which is why voice mode used to have no reliable GUI feedback
        (issue #654).
        """
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            self.webview_window.evaluate_js(
                f'(window.handleVoiceState && window.handleVoiceState({self._safe_json_dumps(payload)}))'
            )
        except Exception:
            pass

    def push_voice_availability(self, payload: dict):
        """Push whether voice mode is armed, and if not why, to JS."""
        self.last_voice_availability = payload
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            self.webview_window.evaluate_js(
                f'(window.handleVoiceAvailability && window.handleVoiceAvailability({self._safe_json_dumps(payload)}))'
            )
        except Exception:
            pass

    def push_dictation_result(self, payload: dict):
        """Push an auto-finished composer dictation transcript to JS.

        Used when silence or the max-duration watchdog ends a recording so the
        React composer can append text and leave the listening state without a
        second mic click. Manual stop still returns via ``stop_dictation``.
        """
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            self.webview_window.evaluate_js(
                f'(window.handleDictationResult && window.handleDictationResult({self._safe_json_dumps(payload)}))'
            )
        except Exception:
            pass

    def push_log(self, message: str, level: str):
        """Push log entry to JavaScript live logs."""
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            import json
            import time
            entry = {
                "timestamp": time.strftime("%H:%M:%S"),
                "level": level,
                "message": message
            }
            try:
                json_str = self._safe_json_dumps(entry)
                self.webview_window.evaluate_js(f'window.handleLogEntry({json_str})')
            except Exception:
                pass
                
    def push_fast_track_commands(self, commands: list):
        """Push the allowlisted '/<alias>' fast-track commands (received at AUTH success)
        so the chat input can offer them without a round-trip per keystroke."""
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            import json
            try:
                json_str = self._safe_json_dumps(commands)
                self.webview_window.evaluate_js(f'window.handleFastTrackCommands({json_str})')
            except Exception:
                pass

    def push_planner_enabled(self, enabled: bool):
        """Push planner.enabled capability so the web UI can hide plan mode when off."""
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            try:
                self.webview_window.evaluate_js(
                    f'window.handlePlannerEnabled({json.dumps(bool(enabled))})'
                )
            except Exception:
                pass

    def push_audio_level(self, level: float):
        """Push audio level for visualizer (high frequency)."""
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            try:
                self.webview_window.evaluate_js(f'window.handleAudioLevel({level})')
            except Exception:
                pass
                
    def push_action(self, command: str, response: str):
        """Push action history item."""
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            import json
            import time
            action = {
                "timestamp": time.strftime("%H:%M"),
                "command": command,
                "response": response
            }
            try:
                json_str = self._safe_json_dumps(action)
                self.webview_window.evaluate_js(f'window.handleActionUpdate({json_str})')
            except Exception:
                pass

    def push_device_detected(self, device_name: str, device_id: str):
        """Push device detected event to JavaScript."""
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            try:
                self.webview_window.evaluate_js(f'window.handleDeviceDetected({json.dumps(device_name)}, {json.dumps(device_id)})')
            except Exception:
                pass

    def push_ai_response(self, text: str, conversation_id: Optional[str] = None, message_id: Optional[str] = None, metadata: Optional[dict] = None):
        """Push a final AI chat response message to the UI."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
            
        self._flush_status_text()
        
        # Ensure UI state reflects readiness
        self.update_status(state="ready")
        
        import json
        try:
            json_str = self._safe_json_dumps(text)
            safe_conv_id = json.dumps(conversation_id) if conversation_id else "null"
            safe_msg_id = json.dumps(message_id) if message_id else "null"
            safe_metadata = json.dumps(metadata) if metadata else "null"
            self.webview_window.evaluate_js(f'window.addAIMessageToChat({json_str}, {safe_conv_id}, {safe_msg_id}, {safe_metadata})')
        except Exception as e:
            print(f"Error pushing AI response: {e}")

    def push_tool_approval_request(self, payload: dict):
        """Push a generic tool-approval request (accept/decline or MCQ) to the chat UI."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            json_str = self._safe_json_dumps(payload)
            self.webview_window.evaluate_js(f'window.addToolApprovalToChat({json_str})')
        except Exception as e:
            logger.error(f"Error pushing tool approval request: {e}")

    def update_conversation_in_store(self, conversation: dict):
        """Push an updated conversation object to the React store."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        import json
        try:
            json_str = json.dumps(conversation)
            self.webview_window.evaluate_js(f'window.updateConversationInStore({json_str})')
        except Exception as e:
            logger.error(f"Error updating conversation in store: {e}")

    def push_artifact(self, artifact_data: dict):
        """
        Push artifact data to the frontend.
        
        Args:
            artifact_data: Dict containing artifact details:
                          {id, type, title, content, status}
        """
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            try:
                # Serialize to JSON - handles escaping and non-serializable objects
                json_str = self._safe_json_dumps(artifact_data)
                self.webview_window.evaluate_js(f'window.handleArtifact({json_str})')
            except Exception as e:
                print(f"Error pushing artifact: {e}")

    def push_turn_mode(self, mode_data: dict):
        """Push resolved turn lane to the React mode dropdown (Auto → Gemma classification)."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            json_str = self._safe_json_dumps(mode_data)
            self.webview_window.evaluate_js(f'window.handleTurnModeResolved?.({json_str})')
        except Exception as e:
            print(f"Error pushing turn mode: {e}")

    # Legacy thinking-channel types the main window's JS handler actually reads
    # (see streamRuntime.ts:handleThinkingEvent). Everything else — reasoning,
    # tool events, plan snapshots, speech — now renders from the typed
    # window.applyStreamEvent seam via push_stream_event, so shipping it here too
    # would just be serialized + evaluate_js'd across the bridge and discarded
    # client-side. Filtering here (the emitter) instead of in JS avoids paying that
    # cost at all. This is NOT a deprecation of the channel or this method — gui_limb
    # still calls push_thinking_event() for every event type; this just stops
    # shipping the ones nobody on this window reads. Does NOT affect the HUD
    # overlay: it's a separate Electron process (chad-hud/) on its own websocket
    # transport (brainSocket.ts), not this pywebview bridge. Full writeup:
    # docs/features/STREAMING_REFACTOR.md ("push_thinking_event is NOT dead").
    # See issue #636.
    _BRIDGE_THINKING_EVENT_TYPES = frozenset({'autonomous_notification', 'model_loading'})

    def push_thinking_event(self, event_data: dict):
        """
        Push an autonomous-notification or model_loading event to the main window
        over the pywebview bridge (window.handleThinkingEvent). All other event
        types are dropped before serialization — see _BRIDGE_THINKING_EVENT_TYPES.
        """
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return

        e_type = event_data.get('type')
        if e_type not in self._BRIDGE_THINKING_EVENT_TYPES:
            return

        import time
        import uuid as _uuid

        # Stable id + sequence for idempotent client-side ingestion
        self._thinking_event_sequence += 1
        if 'id' not in event_data:
            event_data = {**event_data, 'id': str(_uuid.uuid4())}
        if 'sequence' not in event_data:
            event_data = {**event_data, 'sequence': self._thinking_event_sequence}

        try:
            if 'timestamp' not in event_data:
                event_data['timestamp'] = int(time.time() * 1000)
            json_str = self._safe_json_dumps(event_data)
            self.webview_window.evaluate_js(f'window.handleThinkingEvent({json_str})')
        except Exception as e:
            print(f"Error pushing thinking event ({e_type}): {e}")

    def push_abort_active_turn(self):
        """Tell the React client to drop the in-flight typed turn (user hit Stop)."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            self.webview_window.evaluate_js('window.abortActiveTurn && window.abortActiveTurn()')
        except Exception:
            pass

    def push_stream_event(self, events: list):
        """Streaming refactor: push a batch of typed StreamEvents to the React
        reducer via window.applyStreamEvent (streamReducer folds them into streamTurn,
        ChatStream renders from it). Runs PARALLEL to the legacy window.handle* path.
        The typeof guard keeps it safe if an older client bundle lacks the handler.
        Emitted when the Limb has CHAD_STREAM_EVENTS enabled (default on).
        See docs/features/STREAMING_REFACTOR.md.
        """
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        if not events:
            return
        try:
            json_str = self._safe_json_dumps(events)
            # typeof guard: silently no-ops until applyStreamEvent exists client-side.
            self.webview_window.evaluate_js(
                f'(window.applyStreamEvent && window.applyStreamEvent({json_str}))'
            )
        except Exception as e:
            mw_logger.debug(f"[ STREAM ] push_stream_event skipped: {e}")

    def push_token_usage(self, usage_data: dict):
        """Push token telemetry updates to JavaScript."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        
        try:
            json_str = self._safe_json_dumps(usage_data)
            js_call = f'window.handleTokenUsage({json_str})'
            self.webview_window.evaluate_js(js_call)
        except Exception as e:
            logger.error(f"[ MAINWIN ] Error pushing token usage: {e}")

    def push_mcp_install_request(self, server_id: str, title: str, reason: str = None):
        """Ask the React UI to open the MCP install dialog for a specific server."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            payload = json.dumps({"server_id": server_id, "title": title, "reason": reason})
            self.webview_window.evaluate_js(f'window.handleMCPInstallRequest({payload})')
        except Exception as e:
            logger.error(f"[ MAINWIN ] Error pushing MCP install request: {e}")

    def push_capability_suggestion(self, suggestion: dict):
        """Push a newly raised capability suggestion to the React Suggestions panel."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            payload = self._safe_json_dumps(suggestion)
            self.webview_window.evaluate_js(f'window.handleCapabilitySuggestion({payload})')
        except Exception as e:
            logger.error(f"[ MAINWIN ] Error pushing capability suggestion: {e}")

    def push_reflection_lesson(self, lesson: dict):
        """Push a newly raised reflection lesson/question to the React Reflection panel."""
        if self._shutdown or not self.webview_window or not self._webview_ready:
            return
        try:
            payload = self._safe_json_dumps(lesson)
            self.webview_window.evaluate_js(f'window.handleReflectionLesson({payload})')
        except Exception as e:
            logger.error(f"[ MAINWIN ] Error pushing reflection lesson: {e}")

    def push_device_switched(self, device_name: str):
        """Push device switched event to JavaScript."""
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            try:
                self.webview_window.evaluate_js(f'window.handleDeviceSwitched({json.dumps(device_name)})')
            except Exception:
                pass

    def push_input_device_switched(self, device_name: str):
        """Push input device switched event to JavaScript."""
        if self._shutdown:
            return
        if self.webview_window and self._webview_ready:
            try:
                self.webview_window.evaluate_js(f'window.handleInputDeviceSwitched({json.dumps(device_name)})')
            except Exception:
                pass

    # Legacy aliases (deprecated but kept for compatibility if needed)
    _set_js_state = push_state
    _set_js_caption = push_caption

    # ================================================================
    # DIRECT STATE & CAPTION INJECTION (Debugging Pipeline)
    # Bypasses 100ms polling for instant UI updates
    # ================================================================
    
    def _set_js_state(self, state: str):
        """
        Force-inject state directly to JavaScript, bypassing API polling.
        This provides instant visual feedback for debugging state transitions.
        
        Args:
            state: State enum string (idle, listening, thinking, speaking, etc.)
        """
        if self.webview_window:
            try:
                self.webview_window.evaluate_js(f'window.debug_forceState({json.dumps(state)})')
            except Exception as e:
                # Silently fail - webview might not be ready
                pass
    
    def _set_js_caption(self, text: str):
        """
        Force-inject caption text directly to JavaScript, bypassing API polling.
        This provides instant caption updates for debugging.
        
        Args:
            text: Caption text to display
        """
        if self.webview_window:
            try:
                self.webview_window.evaluate_js(f'window.debug_forceCaption({json.dumps(text)})')
            except Exception as e:
                # Silently fail - webview might not be ready
                pass
    
    def toggle_settings(self):
        """Toggle settings panel."""
        pass
    
    def on_close(self):
        """Handle window closing."""
        # Set shutdown flag FIRST to stop all JS calls
        self._shutdown = True
        self._webview_ready = False
        
        # Signal that application should stop
        if hasattr(self, '_app_ref') and self._app_ref():
            self._app_ref().is_running = False
        
        if WEBVIEW_AVAILABLE and self.webview_window:
            try:
                import webview
                webview.destroy_window(self.webview_window)
                self.webview_window = None
            except:
                pass
        self.destroy()
        # Fix 'cannot schedule new futures after shutdown' error
        # Check if loop is not closed before attempting to stop it
        if self.loop and not self.loop.is_closed():
            try:
                self.loop.stop()
            except:
                pass


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Settings")
        self.geometry("400x300")
        self.resizable(False, False)
        
        self.label = ctk.CTkLabel(self, text="Settings", font=FONTS['header'])
        self.label.pack(pady=20)
        
        self.wakeword_label = ctk.CTkLabel(self, text="Wake Word:")
        self.wakeword_label.pack(pady=5)
        
        self.wakeword_menu = ctk.CTkOptionMenu(self, values=["jarvis", "computer", "alexa"])
        self.wakeword_menu.set("jarvis")
        self.wakeword_menu.pack(pady=5)
        
        self.save_btn = ctk.CTkButton(self, text="Save", command=self.save)
        self.save_btn.pack(pady=20)
        
    def save(self):
        self.destroy()
