"""
Live GUI E2E Verification Test
Spawns the real backend server.py as a background subprocess,
launches the real PyWebView VoiceAssistantApp (with Brain Uplink enabled),
enables high debug logging, programmatically injects "Hi" once initialized,
and verifies the model's live response.
"""

import os
import sys
import time
import subprocess
import threading
import logging
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

# Enable high-verbosity debug logging for this test run only
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(name)s:%(lineno)d | %(message)s",
    force=True
)

# Force Cloud/Uplink mode environment overrides for full architecture testing
os.environ["CHAD_MODE"] = "cloud"
os.environ["UPLINK"] = "1"
os.environ["CHAD_UPLINK"] = "true"
os.environ["CHAD_SERVER_URL"] = "ws://localhost:8767/ws"

import webview
from src.clients.desktop.app_controller import VoiceAssistantApp

server_process = None
captured_data = {
    "response_text": None,
    "status": "failure"
}

def start_backend_server():
    """Starts the Chad Cloud Server in the background and monitors its output."""
    global server_process
    logger = logging.getLogger("LiveE2ETest")
    logger.info("Starting backend server (src/remote/server.py) in background...")
    
    # Run server.py as a subprocess
    server_process = subprocess.Popen(
        [sys.executable, "src/remote/server.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(project_root)
    )
    
    # Thread to read and print server output in real-time
    def read_server_output():
        for line in iter(server_process.stdout.readline, ""):
            print(f"[SERVER] {line.strip()}")
            if "listening on" in line or "Uvicorn running" in line:
                logger.info("Backend server signaled readiness!")
                
    threading.Thread(target=read_server_output, daemon=True).start()
    
    # Wait 8 seconds for server to fully initialize and bind port
    logger.info("Waiting 8 seconds for server to warm up...")
    time.sleep(8.0)

def gui_callback_handler(app: VoiceAssistantApp):
    """
    Runs in a background thread after the PyWebView window is loaded.
    """
    logger = logging.getLogger("LiveE2ETest")
    logger.info("Background callback started. Waiting for application initialization...")
    
    # 1. Wait for application initialization
    start_wait = time.time()
    while not app.is_initialized:
        if time.time() - start_wait > 120.0:
            logger.error("Application initialization timed out after 120 seconds.")
            cleanup_and_exit(app)
            return
        time.sleep(0.5)
        
    logger.info("Application initialized successfully! Waiting 5 seconds for uplink connection to stabilize...")
    time.sleep(5.0)
    
    logger.info("Injecting user message: 'Hi'")
    print("\n" + "="*80)
    print("INJECTING USER MESSAGE: 'Hi'")
    print("="*80 + "\n")
    
    # Reset turn completed event if any
    if hasattr(app.window, 'turn_completed_event'):
        app.window.turn_completed_event.clear()
        
    # Inject the message programmatically
    app.window.webview_api.send_text_command("Hi")
    
    # Wait for completion
    logger.info("Waiting for model response...")
    start_turn = time.time()
    
    # We poll turn completed event or wait up to 120 seconds
    is_done = False
    if hasattr(app.window, 'turn_completed_event'):
        is_done = app.window.turn_completed_event.wait(timeout=120.0)
    else:
        # Fallback polling if event not attached
        time.sleep(20.0)
        is_done = True
        
    if is_done:
        logger.info(f"Model responded successfully! Turn completed in {time.time() - start_turn:.2f}s")
        print("\n" + "="*80)
        print("SUCCESS: MODEL RESPONDED!")
        print("="*80 + "\n")
    else:
        logger.error("Model response timed out after 120 seconds.")
        
    # Write captured data to tests/data/live_e2e_output.json
    try:
        data_dir = Path(project_root) / "tests" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        output_file = data_dir / "live_e2e_output.json"
        import json
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(captured_data, f, indent=2)
        logger.info(f"Programmatic live trace caught and written to {output_file}")
    except Exception as e:
        logger.error(f"Failed to write output file: {e}")
        
    # Keep the window open for 5 seconds so the user can see the response on screen
    logger.info("Keeping window open for 5 seconds so you can see the response...")
    time.sleep(5.0)
    
    cleanup_and_exit(app)

def cleanup_and_exit(app):
    """Cleans up the GUI and the background server process."""
    logger = logging.getLogger("LiveE2ETest")
    logger.info("Closing application window.")
    if hasattr(app.window, 'webview_window') and app.window.webview_window:
        app.window.webview_window.destroy()
        
    global server_process
    if server_process:
        logger.info("Terminating background server process...")
        server_process.terminate()
        try:
            server_process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            server_process.kill()
        logger.info("Server process cleaned up successfully.")

def main():
    logger = logging.getLogger("LiveE2ETest")
    
    # 1. Start the backend server in background
    start_backend_server()
    
    logger.info("Starting Live GUI E2E Test...")
    
    # Override webview.start to pass our background callback
    original_start = webview.start
    
    def mocked_start(*args, **kwargs):
        # Inject our test callback to run once the window is up
        kwargs["func"] = gui_callback_handler
        kwargs["args"] = (app,)
        return original_start(*args, **kwargs)
        
    webview.start = mocked_start
    
    # Initialize the real application
    app = VoiceAssistantApp()
    
    # Attach a thread completed event to MainWindow if it doesn't exist
    if not hasattr(app.window, 'turn_completed_event'):
        app.window.turn_completed_event = threading.Event()
        
    # Patch evaluate_js to trigger our completion event on addAIMessageToChat
    original_evaluate_js = app.window.evaluate_js if hasattr(app.window, 'evaluate_js') else None
    
    def mocked_evaluate_js(script):
        if "addAIMessageToChat" in script:
            logger.info("Detected addAIMessageToChat push! Signaling turn completion.")
            try:
                import json
                start_idx = script.find("(") + 1
                end_idx = script.rfind(",")
                if end_idx == -1:
                    end_idx = script.rfind(")")
                json_str = script[start_idx:end_idx]
                response_text = json.loads(json_str)
                captured_data["response_text"] = response_text
                captured_data["status"] = "success"
                logger.info(f"Successfully captured AI response: '{response_text}'")
            except Exception as ex:
                logger.error(f"Failed to parse response text from JS: {ex}")
            app.window.turn_completed_event.set()
        if original_evaluate_js:
            return original_evaluate_js(script)
            
    app.window.evaluate_js = mocked_evaluate_js
    
    # Run the application (this blocks until window is closed)
    try:
        app.run()
    except Exception as e:
        logger.critical(f"Application crashed: {e}")
        cleanup_and_exit(app)
        sys.exit(1)

if __name__ == "__main__":
    main()
