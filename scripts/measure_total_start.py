import time
import sys
import os

start = time.perf_counter()

# Simulate main_gui logic
sys.path.append(os.getcwd())
os.environ['WEBVIEW2_DEFAULT_BACKGROUND_COLOR'] = '0'
os.environ['WEBVIEW2_USER_DATA_FOLDER'] = "data/webview_cache_temp"

import asyncio
import webview
# Force GUI not to open real windows instantly
webview.start = lambda *a, **k: None

from src.clients.desktop.app_controller import VoiceAssistantApp

app_load_point = time.perf_counter()

print(f"\n--- Python Application Boot Metrics ---")
print(f"[ STARTUP ] Module Loading Time: {(app_load_point - start)*1000:.2f}ms")

try:
    # Instantiate controller
    import src.core.utils.system_utils
    def mock_init(s, l="test.lock"):
        s.pid = os.getpid()
        from pathlib import Path
        s.lock_file = Path(l)
        s._suicide_timer = None
    src.core.utils.system_utils.ProcessGuard.__init__ = mock_init
    src.core.utils.system_utils.ProcessGuard.enforce_singleton = lambda s: None
    
    instantiation_start = time.perf_counter()
    app = VoiceAssistantApp()
    instantiation_end = time.perf_counter()
    
    print(f"[ STARTUP ] App Instance Build: {(instantiation_end - instantiation_start)*1000:.2f}ms")
    print(f"[ STARTUP ] Total Readiness: {(instantiation_end - start)*1000:.2f}ms")
except Exception as e:
    print(f"[ ERROR ] Could not complete instantiation: {e}")
