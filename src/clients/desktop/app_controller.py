import sys
import asyncio
import threading
import atexit
import os
import uuid
import logging
from src.clients.desktop.main_window import MainWindow
from src.clients.desktop.components.error_handler import ErrorOverlay
from src.shared.exceptions import VoiceAssistantError, WakeWordError, ModelError, AudioError
from src.shared.logger import setup_logger, add_gui_handler
from src.core.utils.system_utils import ProcessGuard
from src.shared.config_manager import ConfigManager

try:
    from src.shared.benchmark import mark
except ImportError:
    mark = lambda x: None

logger = logging.getLogger("AppController")

class VoiceAssistantApp:
    """
    Main controller for the Voice Assistant Desktop Application.
    Manages process lifecycles, configuration states, cloud mode limbs, and teardowns.
    """
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Heavy AI package warming removed. GUI boots instantly.
        
        self.window = MainWindow(loop=self.loop, app_reference=self)
        
        # Store reference
        import weakref
        self.window._app_ref = weakref.ref(self)
        
        self.error_overlay = ErrorOverlay(self.window)
        
        self.uplink_limb = None
        self.hands_process = None
        self.voice_mode_host = None
        
        self.is_running = True
        self.is_initialized = False
        self._shutdown_initiated = False
        
        self.voice_mode_enabled = ConfigManager.get('session.default_voice_mode', False)

        # Process Protection
        self.process_guard = ProcessGuard()
        self.process_guard.enforce_singleton()
        self.process_guard.register_signal_handlers(self._on_guard_signal)
        
        atexit.register(self._sync_shutdown)
        
        # Setup Logger routing
        def log_callback(message: str, level: str):
            self.window.push_log(message, level)
            
        root_logger = logging.getLogger()
        add_gui_handler(root_logger, log_callback)
        add_gui_handler(logger, log_callback)
        
        self.window.update_status(state="initializing")
        
        self._start_async_initialization()
        
    def _start_async_initialization(self):
        threading.Thread(target=self._run_async_loop, daemon=True).start()
        
    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self.initialize_and_run())
        self.loop.run_forever()
    
    async def _background_initialize(self):
        try:
            # 1. AUTOMATICALLY START THE STANDALONE HANDS DAEMON (OS Controls)
            logger.warning("[ SYSTEM ] : Spinning up external Hands Daemon background pipeline...")
            from src.clients.desktop.helper_process import start_hands_daemon
            start_hands_daemon()
            self.hands_process = None  # hands.py enforces its own singleton
            logger.info("[ SYSTEM ] : Detached Hands Daemon process invoked successfully.")

            # 2. BOOT EYES CLIENT (Pure Remote Frontend Uplink)
            from src.remote.gui_limb import GUIChadLimb
            logger.warning("[ SYSTEM ] : Establishing remote display uplink to Brain...")
            self.window.push_log("Authenticating with Brain Server...", "INFO")
            
            # Define a zero-cost registry stub to satisfy protocol requirements
            class LightweightRegistryStub:
                async def execute_tool(self, name, params):
                    logger.warning(f"Eyes client received unexpected tool call: {name}")
                    return {"success": False, "error": "Eyes client runs in pure display mode."}
                
                async def discover_mcp_servers(self, mcp_registry, sync_cloud=False):
                    pass
            
            self.uplink_limb = GUIChadLimb(self.window, tool_registry=LightweightRegistryStub())
            self.loop.create_task(self.uplink_limb.connect())

            # 3. VOICE MODE (discrete: wake -> STT -> Brain turn -> TTS). Opt-in via
            # voice_mode.enabled; the Limb owns the mic/speaker for the headless Brain.
            try:
                from src.clients.desktop.voice_mode_host import VoiceModeHost
                self.voice_mode_host = VoiceModeHost(
                    uplink_limb=self.uplink_limb,
                    window=self.window,
                    loop=self.loop,
                    config=ConfigManager.load_config(),
                )
                self.loop.create_task(self.voice_mode_host.start())
            except Exception as e:
                logger.warning(f"[ SYSTEM ] : Voice mode host failed to start (non-critical): {e}")

            self.is_initialized = True
            
            # Flush any messages that were sent during the instant-boot time
            if hasattr(self, '_pending_messages') and self._pending_messages:
                logger.info(f"[ SYSTEM ] : Processing {len(self._pending_messages)} queued message(s)...")
                for pending in self._pending_messages:
                    if isinstance(pending, dict):
                        self.window.webview_api.send_text_command(**pending)
                    else:
                        self.window.webview_api.send_text_command(pending)
                self._pending_messages.clear()
            # The Limb's status callbacks will drive connection and ready states dynamically.
            pass
            
        except Exception as e:
            logger.error(f"Limb boot process failed: {e}", exc_info=True)
            self.window.update_status(state="error", text="Couldn't start up — check the logs")

    async def initialize_and_run(self):
        await self._background_initialize()

    async def _async_set_voice_mode(self, enabled: bool):
        self.voice_mode_enabled = enabled
        logger.info("[ SYSTEM ] : Voice toggle triggered.")
        if enabled:
            self.window.update_status(state="ready", text="Voice Streaming Ready")
        else:
            self.window.update_status(state="ready", text="Text Mode Active")

    def set_voice_mode(self, enabled: bool):
        if self.loop and not self.loop.is_closed():
            self.loop.create_task(self._async_set_voice_mode(enabled))
            
    def run(self):
        if hasattr(self.window, 'webview_window') and self.window.webview_window:
            try:
                import webview
                webview.settings['ALLOW_DOWNLOADS'] = True
                user_data_path = os.path.join(os.getcwd(), "data", "webview_cache")
                webview.start(debug=bool(ConfigManager.get('dev_debug', False)), private_mode=False, storage_path=user_data_path)
            except Exception as e:
                logger.error(f"Error starting webview: {e}")
                self.window.deiconify()
                self.window.mainloop()
        else:
            self.window.mainloop()
        
        self._sync_shutdown()
    
    def _on_guard_signal(self):
        logger.warning(f"[ SHUTDOWN ] : Signal received via ProcessGuard.")
        self.is_running = False
        self._sync_shutdown()
        sys.exit(0)
    
    def _sync_shutdown(self):
        if self._shutdown_initiated: return
        self._shutdown_initiated = True
        
        if hasattr(self, 'process_guard'):
            self.process_guard.hard_kill_self()
        
        if self.loop and not self.loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(self.shutdown(), self.loop)
                future.result(timeout=10.0)
            except Exception as e:
                logger.error(f"[ SHUTDOWN ] : Async shutdown error: {e}")
        
        if self.loop and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except: pass
            
        logger.warning("[ SHUTDOWN ] : Shutdown complete.")
    
    async def shutdown(self):
        logger.info("[ SHUTDOWN ] : Disconnecting remote uplink pipeline...")
        if getattr(self, 'voice_mode_host', None):
            try:
                await self.voice_mode_host.stop()
            except Exception as e:
                logger.error(f"[ SHUTDOWN ] : Error stopping voice mode: {e}")
        if hasattr(self, 'uplink_limb') and self.uplink_limb:
            try:
                self.uplink_limb.is_running = False
                if self.uplink_limb.websocket:
                    await self.uplink_limb.websocket.close()
            except Exception as e:
                logger.error(f"[ SHUTDOWN ] : Error closing uplink socket: {e}")
        
        if hasattr(self, 'process_guard'):
            self.process_guard.cleanup()
