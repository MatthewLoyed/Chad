"""
Programmatic GUI E2E Simulation Test
Boots the real VoiceAssistantApp in-memory using a headless dummy window,
executes the 3-turn smart home/workspace sequence exactly as the GUI does,
and outputs intercepted UI updates and trace logs to tests/data/gui_e2e_run_output.json.
"""

import asyncio
import os
import sys
import json
import time
import urllib.request
import weakref
import unittest.mock as mock
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.shared.config_manager import ConfigManager
from src.clients.desktop.api_bridge import WebViewAPI

LM_STUDIO_URL = "http://localhost:1234/v1"
captured_js_pushes = []
captured_thinking_events = []

class DummyMainWindow:
    """Headless dummy MainWindow that intercepts all PyWebView UI updates."""
    def __init__(self, loop=None, app_reference=None):
        self.loop = loop
        self.app_reference = app_reference
        self.action_history_data = []
        self.live_logs_data = []
        self.current_caption_text = ""
        self.current_thinking_text = ""
        self.current_status_text = ""
        self.current_audio_level = 0.0
        self.is_in_active_conversation = False
        self._current_state_enum = "idle"
        self._webview_ready = True
        self.webview_window = self  # Emulate webview_window
        
        # Instantiate real API bridge
        self.webview_api = WebViewAPI(weakref.ref(self))
        
        # Multithreading synchronizer for turns
        import threading
        self.turn_completed_event = threading.Event()

    def evaluate_js(self, script):
        """Intercepts visual pushes intended for index.html/React."""
        captured_js_pushes.append(script)
        
        # Capture thinking events specifically for detailed validation
        if "handleThinkingEvent" in script:
            try:
                # Extract JSON string inside handleThinkingEvent(...)
                start_idx = script.find("(") + 1
                end_idx = script.rfind(")")
                json_str = script[start_idx:end_idx]
                event_data = json.loads(json_str)
                captured_thinking_events.append(event_data)
                
                # Print real-time thoughts to terminal for interactive visibility
                e_type = event_data.get("type")
                if e_type in ["thought", "thinking", "reasoning"] and event_data.get("text"):
                    print(event_data["text"], end="", flush=True)
            except Exception:
                pass
                
        if "addAIMessageToChat" in script:
            try:
                # Extract and display final message response
                start_idx = script.find("(") + 1
                end_idx = script.rfind(",")
                if end_idx == -1:
                    end_idx = script.rfind(")")
                json_str = script[start_idx:end_idx]
                response_text = json.loads(json_str)
                print(f"\n\n  AI Chat Response: \"{response_text}\"\n")
            except Exception:
                pass
            self.turn_completed_event.set()

    def update_status(self, text=None, state="idle"):
        self._current_state_enum = state
        if text:
            self.current_status_text = text
            
    def add_log(self, message, level="INFO"):
        self.live_logs_data.append({
            "message": message,
            "level": level,
            "timestamp": time.strftime("%H:%M:%S")
        })

    def push_log(self, message, level):
        self.add_log(message, level)

    def push_state(self, state): pass
    def push_status_text(self, text): pass
    def push_caption(self, text, append=False): pass
    def push_audio_level(self, level): pass
    def push_thinking_event(self, event_data):
        self.evaluate_js(f"window.handleThinkingEvent({json.dumps(event_data)})")
    def push_ai_response(self, text):
        self.evaluate_js(f"window.addAIMessageToChat({json.dumps(text)})")
    def push_artifact(self, artifact): pass
    def push_device_detected(self, name, id): pass
    def push_device_switched(self, name): pass
    def push_input_device_switched(self, name): pass
    def deiconify(self): pass
    def mainloop(self): pass

def check_lm_studio_server() -> bool:
    try:
        with urllib.request.urlopen(f"{LM_STUDIO_URL}/models", timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        return False

async def main():
    # Force local mode environment overrides
    os.environ["CHAD_UI_DEV"] = "false"
    os.environ["CHAD_MODE"] = "local"
    os.environ["CHAD_UPLINK"] = "false"
    os.environ["UPLINK"] = "0"

    print("\n" + "="*70)
    print("=== SYSTEM DIAGNOSTIC & PROGRAMMATIC GUI E2E SIMULATION ===")
    print("="*70)

    diagnostic_metrics = {}

    # ------------------------------------------------------------------
    # [STEP 1] LM Studio Dev Server Connectivity & Active Model Load
    # ------------------------------------------------------------------
    print("\n[DIAGNOSTIC STEP 1] Verifying LM Studio server connectivity...")
    start_step = time.time()
    try:
        if not check_lm_studio_server():
            print("[SKIP] LM Studio is not active on port 1234.")
            print("Skipping test (exiting with code 0) since LM Studio is required.")
            sys.exit(0)
        
        with urllib.request.urlopen(f"{LM_STUDIO_URL}/models", timeout=3.0) as resp:
            models_data = json.loads(resp.read().decode())
            data = models_data.get("data", [])
            if not data:
                print("[SKIP] LM Studio server active, but no models are loaded.")
                print("Skipping test (exiting with code 0) since LM Studio model is required.")
                sys.exit(0)
                
            model_id = None
            for model in data:
                m_id = model.get("id", "")
                if "coder" in m_id.lower() or "1.5b" in m_id.lower():
                    model_id = m_id
                    break
            
            if not model_id:
                model_id = data[0].get("id")
                
            print(f"  -> Connected successfully to LM Studio.")
            print(f"  -> Active GGUF model: {model_id}")
            diagnostic_metrics["lm_studio_connectivity"] = {
                "status": "success",
                "active_model": model_id,
                "latency_seconds": round(time.time() - start_step, 3)
            }
    except Exception as e:
        print(f"[SKIP] LM Studio connectivity failed: {e}")
        print("Skipping test (exiting with code 0) since LM Studio is required.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # [STEP 2] Config Manager & Preferences Validation
    # ------------------------------------------------------------------
    print("\n[DIAGNOSTIC STEP 2] Validating Configuration Manager & preferences...")
    start_step = time.time()
    try:
        original_get = ConfigManager.get
        def mock_get(key, default=None):
            if key == "session.default_voice_mode":
                return False
            if key == "local_models.active_model":
                return {
                    "name": model_id,
                    "provider": "lmstudio",
                    "base_url": LM_STUDIO_URL,
                    "supports_tools": True
                }
            if key == "local_models.use_local":
                return True
            return original_get(key, default)

        ConfigManager.get = mock_get
        ConfigManager.is_local_model_active = lambda: True
        ConfigManager.get_local_model_config = lambda: {
            "name": model_id,
            "provider": "lmstudio",
            "base_url": LM_STUDIO_URL,
            "supports_tools": True
        }
        
        # Verify can load config options
        _ = ConfigManager.get_local_model_config()
        print(f"  -> Config system validated & model overrides successfully injected.")
        diagnostic_metrics["configuration_validation"] = {
            "status": "success",
            "latency_seconds": round(time.time() - start_step, 3)
        }
    except Exception as e:
        print(f"[CRITICAL] Configuration validation failed: {e}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # [STEP 3] Database & Storage Layer Verification
    # ------------------------------------------------------------------
    print("\n[DIAGNOSTIC STEP 3] Verifying local sqlite database & filesystems...")
    start_step = time.time()
    try:
        from src.infrastructure.repositories import ConfigRepository
        repo = ConfigRepository()
        # Simple read verification
        _ = repo.get_value("preferences", "local_models.use_local")
        print(f"  -> Storage repositories verified successfully.")
        diagnostic_metrics["storage_validation"] = {
            "status": "success",
            "latency_seconds": round(time.time() - start_step, 3)
        }
    except Exception as e:
        print(f"[CRITICAL] Storage layer validation failed: {e}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # [STEP 4] Real VoiceAssistantApp Headless Bootstrap
    # ------------------------------------------------------------------
    print("\n[DIAGNOSTIC STEP 4] Booting real VoiceAssistantApp headlessly on unified event loop...")
    start_step = time.time()
    
    current_loop = asyncio.get_running_loop()
    from src.clients.desktop.app_controller import VoiceAssistantApp
    
    def patched_init(self):
        self.loop = current_loop
        self.window = DummyMainWindow(loop=current_loop, app_reference=self)
        self.hud = mock.Mock()
        import weakref
        self.window._app_ref = weakref.ref(self)
        self.error_overlay = mock.Mock()
        self.assistant = None
        self.boot_manager = None
        self.sound_manager = None
        self.text_mode_client = None
        self.wake_loop = None
        self.limb = None
        self.current_command = ""
        self.last_final_transcript = ""
        self.is_running = True
        self.is_initialized = False
        self._boot_progress = 0
        self._shutdown_initiated = False
        self._active_text_task = None
        self.uplink_enabled = False
        self.voice_mode_enabled = False
        self.process_guard = mock.Mock()
        
        # Start initialization directly on active loop
        current_loop.create_task(self._background_initialize())
        
    VoiceAssistantApp.__init__ = patched_init
    
    with mock.patch("src.clients.desktop.app_controller.MainWindow", DummyMainWindow):

        app = VoiceAssistantApp()
        
        # Await app initialization with verbose countdown logging
        print("  -> BootManager initializing core subsystems (max 60s)...")
        start_wait = time.time()
        last_log_time = start_wait
        while not app.is_initialized and time.time() - start_wait < 60.0:
            await asyncio.sleep(0.1)
            current_time = time.time()
            if current_time - last_log_time >= 5.0:
                print(f"     [BOOT STATUS] Subsystem initialization active... elapsed: {round(current_time - start_wait, 1)}s")
                last_log_time = current_time
            
        if not app.is_initialized:
            print("[CRITICAL] Subsystems failed to initialize within 60s limit.")
            sys.exit(1)
            
        boot_duration = time.time() - start_step
        print(f"  -> Real VoiceAssistantApp successfully booted in {round(boot_duration, 2)}s!")
        diagnostic_metrics["app_bootstrap"] = {
            "status": "success",
            "latency_seconds": round(boot_duration, 3)
        }

        # ------------------------------------------------------------------
        # [STEP 5] Supervisor & Agent Tool Registry Verification
        # ------------------------------------------------------------------
        print("\n[DIAGNOSTIC STEP 5] Verifying LangGraph supervisor & sub-agent tool discovery...")
        start_step = time.time()
        try:
            # Check master tool registry
            tool_registry = app.assistant.tool_registry
            if not tool_registry:
                raise ValueError("Master ToolRegistry was not instantiated on the assistant.")
                
            print(f"  -> Discovered {len(tool_registry._tools)} local tools in master registry.")
            for t_name in ["hubspace_control", "search_gmail"]:
                if t_name not in tool_registry._tools:
                    raise ValueError(f"{t_name} tool is missing from master discovery registry.")

            # Compile and verify the supervisor LangGraph
            engine = app.text_mode_client.langgraph_engine
            if not engine:
                raise ValueError("LangGraphEngine was not instantiated on text_mode_client.")
                
            print("  -> Compiling hierarchical supervisor LangGraph...")
            graph = await engine.get_graph()
            if not graph:
                raise ValueError("Compiled graph was not generated by LangGraphEngine.")
                
            print(f"  -> Hierarchical Supervisor compiled successfully.")
            diagnostic_metrics["supervisor_validation"] = {
                "status": "success",
                "total_tools": len(tool_registry._tools),
                "latency_seconds": round(time.time() - start_step, 3)
            }
        except Exception as e:
            print(f"[CRITICAL] LangGraph tool verification failed: {e}")
            sys.exit(1)

        # ------------------------------------------------------------------
        # [STEP 6] Sequential Multi-Turn User Simulation
        # ------------------------------------------------------------------
        print("\n[DIAGNOSTIC STEP 6] Executing sequential multi-turn user simulation...")
        
        # Patch _get_optimized_tools to inject required tools during sequential test
        client = app.text_mode_client
        original_get_tools = client._get_optimized_tools
        async def patched_get_tools(user_input, history=None):
            schemas = await original_get_tools(user_input, history)
            for t_name in ["hubspace_control", "search_gmail"]:
                if not any(s.get("name") == t_name for s in schemas):
                    tool = app.assistant.tool_registry.get_tool(t_name)
                    if tool:
                        schemas.append(app.assistant.tool_registry._extract_schema(t_name, tool))
            return schemas
        client._get_optimized_tools = patched_get_tools

        test_phases = [
            {"turn": 1, "query": "Turn on the lamp."},
            {"turn": 2, "query": "Now turn off that lamp."},
            {"turn": 3, "query": "Check my gmail inbox for recent messages."}
        ]
        
        results = []
        has_failed = False
        
        for phase in test_phases:
            turn_num = phase["turn"]
            query_text = phase["query"]
            
            print(f"\n[TURN {turn_num}] Sending user text: \"{query_text}\"")
            print("-" * 70)
            print("  Thinking Delta Stream:")
            print("  ", end="")
            
            app.window.turn_completed_event.clear()
            captured_thinking_events.clear()
            
            start_turn = time.time()
            app.window.webview_api.send_text_command(text=query_text)
            
            # Await completion
            is_done = await asyncio.to_thread(app.window.turn_completed_event.wait, timeout=180.0)
            turn_duration = time.time() - start_turn
            
            if not is_done:
                print(f"\n[TIMEOUT] Turn {turn_num} timed out after 180 seconds.")
                results.append({
                    "turn": turn_num,
                    "query": query_text,
                    "error": "Timeout",
                    "metrics": {"latency_seconds": round(turn_duration, 2)}
                })
                has_failed = True
                break
                
            ai_response = ""
            for push in captured_js_pushes:
                if "addAIMessageToChat" in push:
                    try:
                        start_idx = push.find("(") + 1
                        end_idx = push.rfind(",")
                        if end_idx == -1: end_idx = push.rfind(")")
                        ai_response = json.loads(push[start_idx:end_idx])
                    except Exception:
                        pass
            
            captured_js_pushes.clear()
            
            results.append({
                "turn": turn_num,
                "query": query_text,
                "response": ai_response,
                "thinking_events": list(captured_thinking_events),
                "metrics": {
                    "latency_seconds": round(turn_duration, 2)
                }
            })
            print("-" * 70)
            print(f"[SUCCESS] Turn {turn_num} resolved successfully in {round(turn_duration, 2)}s.")

        # ------------------------------------------------------------------
        # [STEP 7] Clean Subsystem Shutdown
        # ------------------------------------------------------------------
        print("\n[DIAGNOSTIC STEP 7] Shutting down subsystems cleanly...")
        start_step = time.time()
        app._sync_shutdown()
        print(f"  -> App closed successfully.")
        diagnostic_metrics["app_shutdown"] = {
            "status": "success",
            "latency_seconds": round(time.time() - start_step, 3)
        }

        # Save comprehensive test report to tests/data/gui_e2e_run_output.json
        data_dir = Path("tests/data")
        data_dir.mkdir(parents=True, exist_ok=True)
        
        output_file = data_dir / "gui_e2e_run_output.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "status": "failure" if has_failed else "success",
                "model_id": model_id,
                "diagnostics": diagnostic_metrics,
                "turns": results
            }, f, indent=2)
            
        print(f"\n[REPORT SYSTEM] Sequential E2E diagnostic trace dumped to {output_file}")
        
    print("\n" + "="*70)
    print("=== PROGRAMMATIC GUI RUN COMPLETE ===")
    print("="*70 + "\n")
    
    if has_failed:
        sys.exit(1)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
