import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Mock modules that app_controller imports which might try to touch tkinter/COM/etc.
with patch("src.clients.desktop.app_controller.MainWindow") as mock_main_window, \
     patch("src.clients.desktop.app_controller.ErrorOverlay") as mock_error_overlay:

    from src.clients.desktop.app_controller import VoiceAssistantApp

def test_webview_start_arguments():
    """
    Verifies that VoiceAssistantApp.run() starts webview with private_mode=False
    and the dedicated local cache storage_path to prevent pywebview from generating
    temporary user data folders that fail to clean up on Windows.
    """
    # Create the app instance
    app = VoiceAssistantApp()
    
    # Mock window
    app.window = MagicMock()
    app.window.webview_window = MagicMock()
    
    # We patch webview module inside run
    with patch("webview.start") as mock_webview_start, \
         patch("webview.settings", {}) as mock_webview_settings:
        
        # We don't want app._sync_shutdown to block or fail
        try:
            with patch.object(app, "_sync_shutdown") as mock_sync_shutdown:
                app.run()

                # Assert webview.settings['ALLOW_DOWNLOADS'] is enabled
                assert mock_webview_settings.get('ALLOW_DOWNLOADS') is True

                # Assert webview.start was called with correct parameters
                mock_webview_start.assert_called_once()
                _, kwargs = mock_webview_start.call_args

                assert "private_mode" in kwargs
                assert kwargs["private_mode"] is False

                assert "storage_path" in kwargs
                expected_path = os.path.join(os.getcwd(), "data", "webview_cache")
                assert kwargs["storage_path"] == expected_path
        finally:
            # app.run() starts a daemon thread running its own asyncio loop
            # forever (self._run_async_loop) to drive uplink_limb.connect()
            # retries etc. Without stopping it, that loop keeps running for
            # the rest of the pytest session, leaking real network retries
            # and background state into unrelated later tests (#216).
            if app.loop and not app.loop.is_closed():
                app.loop.call_soon_threadsafe(app.loop.stop)
