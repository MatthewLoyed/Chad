try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

import logging
import time
import threading
from typing import Optional

try:
    from screeninfo import get_monitors
    HAS_SCREENINFO = True
except ImportError:
    HAS_SCREENINFO = False

# Setup logger
logger = logging.getLogger("Overlay")

class OverlayManager:
    """
    Manages OS-level holographic notifications for CHAD.
    Uses the 'Phantom Window' pattern: creates one window off-screen
    and teleports it into view to avoid native white flashes.
    """
    
    def __init__(self, audio_handler=None, sound_manager=None):
        self.audio_handler = audio_handler
        self.sound_manager = sound_manager
        self.window = None
        self._is_visible = False
        
        # Aesthetic configuration
        self.bg_color = "radial-gradient(circle, #111823 0%, #050505 100%)"
        self.border_color = "#00f3ff"
        self.font_family = "'Share Tech Mono', monospace"
        
        # HTML/JS/CSS Template for the phantom window
        self.template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
            <style>
                html, body {{
                    background: transparent !important;
                    background-color: transparent !important;
                    margin: 0;
                    padding: 0;
                    overflow: hidden;
                    width: 100%;
                    height: 100%;
                    font-family: {self.font_family};
                    color: {self.border_color};
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    user-select: none;
                }}
                
                #notification-container {{
                    width: 420px;
                    height: 140px;
                    background: rgba(13, 25, 40, 0.85);
                    border: 1px solid rgba(0, 243, 255, 0.4);
                    box-shadow: 0 0 30px rgba(0, 243, 255, 0.15), inset 0 0 20px rgba(0, 243, 255, 0.1);
                    padding: 15px;
                    display: flex;
                    flex-direction: row;
                    align-items: center;
                    backdrop-filter: blur(12px);
                    position: relative;
                    opacity: 0;
                    transition: all 0.6s cubic-bezier(0.2, 0.8, 0.2, 1);
                    clip-path: polygon(0 0, 92% 0, 100% 20%, 100% 100%, 8% 100%, 0 80%);
                    transform: translateY(20px) scale(0.95);
                    color: #fff;
                }}

                #notification-container.visible {{
                    opacity: 1;
                    transform: translateY(0) scale(1);
                }}

                #notification-container.fading {{
                    opacity: 0;
                    transform: translateY(-10px) scale(1.05);
                    filter: blur(10px);
                }}

                .icon {{
                    width: 70px;
                    height: 70px;
                    margin-right: 20px;
                    font-size: 44px;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    border-right: 1px solid rgba(0, 243, 255, 0.2);
                    padding-right: 15px;
                    color: {self.border_color};
                    text-shadow: 0 0 15px rgba(0, 243, 255, 0.5);
                }}

                .content {{
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                }}

                .title {{
                    font-size: 20px;
                    font-weight: bold;
                    text-transform: uppercase;
                    margin-bottom: 8px;
                    letter-spacing: 3px;
                    color: #00f3ff;
                    text-shadow: 0 0 10px rgba(0, 243, 255, 0.3);
                }}

                .message {{
                    font-size: 15px;
                    opacity: 0.85;
                    line-height: 1.4;
                }}

                /* Scanning line effect */
                #notification-container::after {{
                    content: "";
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 2px;
                    background: linear-gradient(90deg, transparent, rgba(0, 243, 255, 0.5), transparent);
                    animation: scan 3s linear infinite;
                }}

                @keyframes scan {{
                    0% {{ top: 0%; opacity: 0; }}
                    50% {{ opacity: 1; }}
                    100% {{ top: 100%; opacity: 0; }}
                }}
            </style>
        </head>
        <body>
            <div id="notification-container">
                <div class="icon" id="notif-icon">⚠</div>
                <div class="content">
                    <div class="title" id="notif-title">TITLE</div>
                    <div class="message" id="notif-message">MESSAGE</div>
                </div>
            </div>

            <script>
                const container = document.getElementById('notification-container');
                const titleEl = document.getElementById('notif-title');
                const msgEl = document.getElementById('notif-message');
                const iconEl = document.getElementById('notif-icon');

                function updateContent(title, msg, icon) {{
                    titleEl.innerText = title;
                    msgEl.innerText = msg;
                    if (icon) iconEl.innerText = icon;
                }}

                function playEntrance() {{
                    container.classList.remove('fading');
                    container.classList.add('visible');
                }}

                function playExit() {{
                    container.classList.remove('visible');
                    container.classList.add('fading');
                }}
            </script>
        </body>
        </html>
        """
        
        # LAZY INIT: Do NOT create window here. Defer to first show_notification() call.
        # This saves ~4s at startup by avoiding a second webview instance.
        if HAS_WEBVIEW:
            logger.info("[ OVERLAY ] : OverlayManager created (window deferred to first use).")
        else:
            logger.warning("[ OVERLAY ] : webview module not found. Overlay notifications will be disabled.")


    def _ensure_window(self):
        """Lazily create the phantom window on first use."""
        if not HAS_WEBVIEW:
            return
        if self.window is not None:
            return
        
        self.window = webview.create_window(
            title="CHAD Phantom HUD",
            html=self.template,
            width=400,
            height=150,
            x=-5000,
            y=-5000,
            frameless=True,
            easy_drag=False,
            on_top=True,
            transparent=True,
            background_color='#111823'
        )
        
        logger.warning(f"[ OVERLAY ] : Phantom Window initialized in 'Stealth Mode' (Off-screen).")

    def _get_monitor_1_pos(self, width=420):
        """Get the top-right position of Monitor 1."""
        if not HAS_SCREENINFO:
            return 100, 100
        try:
            monitors = get_monitors()
            if not monitors:
                return 100, 100
            primary = next((m for m in monitors if m.is_primary), monitors[0])
            x = primary.x + primary.width - width - 20
            y = primary.y + 20
            return x, y
        except Exception as e:
            logger.error(f"Error detecting monitor positions: {e}")
            return 100, 100

    def show_notification(self, title: str, message: str, icon: str = "⚠", duration: int = 10):
        """
        Teleports the phantom window into view and plays entrance.
        Uses JS-driven visibility to avoid GUI thread corruption.
        """
        # Lazy init: create window on first notification
        self._ensure_window()
        
        if not self.window:
            logger.error("Overlay window not initialized")
            return

        # Step A: Update Content and Visibility via JS (Fast, Loop-Safe)
        # Use repr() to safely escape strings for JS
        js_code = f"updateContent({repr(title)}, {repr(message)}, {repr(icon)})"
        self.window.evaluate_js(js_code)
        
        # Step B: Teleport into view (Main Thread Safe)
        # ONLY call move if absolutely necessary. Ensure coordinates are strictly integers.
        x, y = self._get_monitor_1_pos()
        target_x, target_y = int(x), int(y)
        
        def safe_move():
            try:
                logger.warning(f"[ OVERLAY ] : Thread-Safe Teleport to ({target_x}, {target_y}).")
                self.window.move(target_x, target_y)
            except Exception as move_e:
                logger.error(f"Thread isolation violation in move(): {move_e}")

        # Wrap move in a safety check or platform-specific call if available
        if hasattr(self.window, 'gui') and hasattr(self.window.gui, 'call_later'):
            self.window.gui.call_later(safe_move)
        else:
            # Fallback to direct move but ensure it's not blocking the main loop's progress
            # In pywebview, move() is usually synchronous but fast.
            safe_move()

        # Step C: Play Entrance (JS-driven opacity)
        self.window.evaluate_js("playEntrance()")
        
        # Async sound play
        if self.audio_handler and hasattr(self.audio_handler, 'loop') and self.audio_handler.loop:
            self.audio_handler.loop.create_task(self.play_alert_sound())
        
        self._is_visible = True
        
        # Step D: Auto-hide using JS-driven timing to avoid Python thread move calls
        def auto_hide_worker():
            # Wait for the notification duration
            time.sleep(duration)
            
            # Use JS to fade out first
            self.window.evaluate_js("playExit()")
            
            # Wait for fade to finish
            time.sleep(1.5)
            
            # Move back off-screen ONLY after it's fully invisible
            try:
                # Direct move call wrapped in int() cast
                target_x, target_y = int(-5000), int(-5000)
                
                def safe_offscreen():
                    try:
                        self.window.move(target_x, target_y)
                    except:
                        pass
                
                if hasattr(self.window, 'gui') and hasattr(self.window.gui, 'call_later'):
                    self.window.gui.call_later(safe_offscreen)
                else:
                    self.window.move(target_x, target_y)
            except Exception as e:
                logger.debug(f"Off-screen move suppressed: {e}")
            
            self._is_visible = False
            logger.warning(f"[ OVERLAY ] : Neutralizing alert after {duration}s.")

        threading.Thread(target=auto_hide_worker, daemon=True).start()

    async def play_alert_sound(self):
        """Play a tactical 'ping' sound (Async)."""
        if not self.audio_handler or not self.sound_manager:
            return
        try:
            audio_bytes = await self.sound_manager.get_sound("notification")
            if audio_bytes:
                self.audio_handler.output.play_sound_directly(audio_bytes)
        except Exception as e:
            logger.error(f"Error playing alert sound: {e}")

    def destroy(self):
        """Clean up and destroy the phantom HUD window."""
        if self.window:
            try:
                self.window.destroy()
                self.window = None
                logger.info("[ OVERLAY ] : Phantom HUD destroyed.")
            except Exception as e:
                logger.debug(f"Error destroying overlay window: {e}")

