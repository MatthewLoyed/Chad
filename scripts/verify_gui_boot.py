import os
import sys
import time
import asyncio
from pathlib import Path

# Force headless/mock behavior
os.environ["PYTHONUNBUFFERED"] = "1"
sys.path.append(os.getcwd())

# 1. Monkey-patch ProcessGuard so we don't kill the user's real process!
import src.core.utils.system_utils
original_init = src.core.utils.system_utils.ProcessGuard.__init__
def mock_init(self, lock_file_name="test_verify_process.lock"):
    self.pid = os.getpid()
    self.lock_file = Path(lock_file_name).resolve()
    self._suicide_timer = None
src.core.utils.system_utils.ProcessGuard.__init__ = mock_init

# 2. Monkey-patch the UI loops so they don't actually open windows
import webview
webview.start = lambda *args, **kwargs: print("[BOOT MOCK] webview.start called, suppressing actual window spawn.")

import src.clients.desktop.main_window
src.clients.desktop.main_window.MainWindow.mainloop = lambda self: print("[BOOT MOCK] mainloop called.")

from src.clients.desktop.app_controller import VoiceAssistantApp

async def run_verify():
    print("\n===== STARTING ISOLATED BOOT VERIFICATION =====")
    print("[*] Initializing Application Controller...")
    try:
        app = VoiceAssistantApp()
        
        print("[*] Controller instance created successfully.")
        print("[*] Monitoring boot progress asynchronously for 15 seconds...")
        
        start_time = time.time()
        while time.time() - start_time < 15:
            progress = getattr(app, '_boot_progress', 0)
            print(f"    [DEBUG] Boot Progress: {progress}%", flush=True)
            if progress >= 100 or app.is_initialized:
                print("\n[SUCCESS] GUI reached FULLY INITIALIZED state!")
                break
            await asyncio.sleep(1.5)
        
        if app.is_initialized:
            print("[*] Verified: System bridges and initialization pipelines flow without error.\n")
        else:
            print("[!] Warning: Boot reached partial completion but did not finish in time limit.")
            
    except Exception as e:
        print(f"\n[FATAL] Crash during boot instantiation: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    print("===== VERIFICATION END =====\n")
    return True

def main():
    # Set event loop policy just like main_gui.py
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_verify())

if __name__ == "__main__":
    main()
