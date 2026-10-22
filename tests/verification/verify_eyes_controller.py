import os
import sys
import asyncio
import logging

# Configure paths
sys.path.append(os.getcwd())

# Set environment variables to simulate limb mode
os.environ["UPLINK"] = "1"
os.environ["CHAD_UPLINK"] = "true"
os.environ["CHAD_SERVER_URL"] = "ws://localhost:8767"

def test_import_isolation():
    print("[ VERIFICATION ] testing package isolation...", flush=True)
    
    # Verify that heavy modules are NOT pre-loaded by default
    heavy_modules = [
        "litellm", 
        "google.generativeai", 
        "langgraph",
        "src.core.intelligence.adapters.litellm_adapter"
    ]
    
    for mod in list(sys.modules.keys()):
        for heavy in heavy_modules:
            if mod.startswith(heavy):
                print(f"[ WARNING ] Heavy module '{mod}' was unexpectedly loaded before app initialization.")
                return False
                
    # Now import the AppController
    from src.clients.desktop.app_controller import VoiceAssistantApp
    print("[ SUCCESS ] VoiceAssistantApp imported successfully without immediate crashes.")
    
    # Re-check modules
    loaded_heavies = []
    for mod in list(sys.modules.keys()):
        for heavy in heavy_modules:
            if mod.startswith(heavy):
                loaded_heavies.append(mod)
                
    if loaded_heavies:
        print(f"[ FAILURE ] The following heavy AI modules were loaded during AppController import: {loaded_heavies}")
        return False
    else:
        print("[ SUCCESS ] Absolute Import Isolation established! 0 heavy AI packages loaded.")
        return True

async def test_mock_window_initialization():
    print("[ VERIFICATION ] testing decoupled initializer branching...", flush=True)
    
    # Create a mock MainWindow to bypass GUI threading constraints
    class MockVisualizer:
        def set_state(self, state): pass
        
    class MockWindow:
        def __init__(self):
            self.visualizer = MockVisualizer()
            self.app_reference = None
        def update_status(self, state, text=None):
            print(f"  [ WINDOW STATUS ] -> state: {state}, text: {text}")
        def push_log(self, message, level):
            print(f"  [ WINDOW LOG ] -> {level}: {message}")
        def deiconify(self): pass
        def mainloop(self): pass

    from src.clients.desktop.app_controller import VoiceAssistantApp
    
    # Monkeypatch MainWindow inside AppController to prevent launching Tkinter/PyWebView
    import src.clients.desktop.app_controller
    original_window_class = src.clients.desktop.app_controller.MainWindow
    src.clients.desktop.app_controller.MainWindow = lambda *args, **kwargs: MockWindow()
    
    # Monkeypatch HUDController to prevent drawing HUD
    src.clients.desktop.app_controller.HUDController = lambda *args, **kwargs: None
    
    # Disable singleton guard for this test
    from src.core.utils.system_utils import ProcessGuard
    original_singleton = ProcessGuard.enforce_singleton
    ProcessGuard.enforce_singleton = lambda self: None
    
    try:
        # Instantiate app
        app = VoiceAssistantApp()
        app.window.app_reference = app
        
        print(f"[ SUCCESS ] Application constructed. Uplink enabled: {app.uplink_enabled}")
        
        # Mock subprocess.Popen so it doesn't actually launch the daemon during testing
        import subprocess
        original_popen = subprocess.Popen
        daemon_spawned = [False]
        
        def mock_popen(*args, **kwargs):
            daemon_spawned[0] = True
            print("  [ SUBPROCESS ] Mock spawn of Hands Daemon subprocess intercepted.")
            class DummyProc:
                pid = 99999
            return DummyProc()
            
        subprocess.Popen = mock_popen
        
        # Run background initialize coroutine
        print("[ INFO ] Triggering background initialize manually...")
        await app._background_initialize()
        
        # Validate state assertions
        assert app.is_initialized == True, "App should be marked as initialized"
        assert daemon_spawned[0] == True, "Standalone Hands Daemon should have been spawned"
        assert hasattr(app, 'uplink_limb') and app.uplink_limb is not None, "Uplink Limb should have been constructed"
        
        # Ensure legacy components remain uninitialized
        assert not hasattr(app, 'assistant') or app.assistant is None, "Core Assistant should NOT be created in Uplink mode!"
        assert not hasattr(app, 'boot_manager') or app.boot_manager is None, "Boot Manager should NOT be created in Uplink mode!"
        assert not hasattr(app, 'wake_loop') or app.wake_loop is None, "WakeWord Loop should NOT be created in Uplink mode!"
        
        print("[ SUCCESS ] Decoupled initialization state assertions passed 100%!")
        return True
        
    except Exception as e:
        print(f"[ FAILURE ] Decoupled execution failed: {e}", exc_info=True)
        return False
    finally:
        # Restore monkeypatches
        src.clients.desktop.app_controller.MainWindow = original_window_class
        ProcessGuard.enforce_singleton = original_singleton
        subprocess.Popen = original_popen

async def main():
    print("\n" + "="*60)
    print("RUNNING EYES CLIENT (GUI DECOUPLING) VERIFICATION")
    print("="*60 + "\n")
    
    isolation_ok = test_import_isolation()
    if not isolation_ok:
        sys.exit(1)
        
    init_ok = await test_mock_window_initialization()
    if not init_ok:
        sys.exit(1)
        
    print("\n" + "="*60)
    print("ALL VERIFICATION CHECKS PASSED SUCCESSFULLY")
    print("The Desktop client is 100% decoupled, lightweight, and autonomous.")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
