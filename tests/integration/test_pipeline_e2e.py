"""
Standalone Local E2E Pipeline Test
Verifies the complete user turn pipeline using a local model in LM Studio.
Outputs execution metrics and traces to tests/data/e2e_run_output.json.
"""

import asyncio
import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.shared.config_manager import ConfigManager
from src.application.events import EventBus
from src.infrastructure.tools.registry import ToolRegistry
from src.core.tools.tool_executor import LocalToolExecutor
from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
from src.core.interfaces.text_mode import TextModeClient
from src.core.intelligence.memory_engine import MemoryEngine

LM_STUDIO_URL = "http://localhost:1234/v1"

def check_lm_studio_server() -> bool:
    """Pings the LM Studio models endpoint to verify if the server is active."""
    try:
        with urllib.request.urlopen(f"{LM_STUDIO_URL}/models", timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        return False

def attempt_start_lm_studio() -> bool:
    """Attempts to launch LM Studio on Windows using standard installation paths."""
    local_app_data = os.path.expandvars("%LocalAppData%")
    program_files = os.path.expandvars("%ProgramFiles%")
    
    possible_paths = [
        Path(local_app_data) / "Programs" / "lm-studio" / "LM Studio.exe",
        Path(program_files) / "LM Studio" / "LM Studio.exe"
    ]
    
    executable_path = None
    for path in possible_paths:
        if path.exists():
            executable_path = path
            break
            
    if not executable_path:
        print("[LM STUDIO] Could not find LM Studio.exe in standard Windows paths.")
        return False

    print(f"[LM STUDIO] Server not running. Attempting to launch: {executable_path}")
    try:
        # Launch LM Studio in background, detached, without terminal window
        subprocess.Popen(
            [str(executable_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        return True
    except Exception as e:
        print(f"[LM STUDIO] Failed to launch executable: {e}")
        return False

async def main():
    print("\n" + "="*70)
    print("=== LOCAL USER-TO-MODEL PIPELINE E2E TEST ===")
    print("="*70)

    # 1. Check or Launch LM Studio
    print("\n[STEP 1] Verifying local LM Studio server connection...")
    if not check_lm_studio_server():
        print("[LM STUDIO] Server is not running on port 1234.")
        launched = attempt_start_lm_studio()
        if not launched:
            print("\n[SKIP] LM Studio is not active and could not be started automatically.")
            print("Skipping test (exiting with code 0) since LM Studio is required.")
            sys.exit(0)
            
        print("[LM STUDIO] Waiting up to 15 seconds for LM Studio server to initialize...")
        server_ok = False
        for i in range(15):
            await asyncio.sleep(1.0)
            print(f"  Pinging port 1234 (attempt {i+1}/15)...")
            if check_lm_studio_server():
                server_ok = True
                break
                
        if not server_ok:
            print("\n[SKIP] LM Studio launched but failed to start its server on port 1234 within 15 seconds.")
            print("Skipping test (exiting with code 0) since LM Studio is required.")
            sys.exit(0)
    else:
        print("[LM STUDIO] Server is already active.")

    # 2. Discover Loaded Models
    print("\n[STEP 2] Querying active GGUF models...")
    try:
        with urllib.request.urlopen(f"{LM_STUDIO_URL}/models", timeout=3.0) as resp:
            models_data = json.loads(resp.read().decode())
            data = models_data.get("data", [])
            if not data:
                print("\n[SKIP] Connection verified, but NO models are currently loaded in LM Studio.")
                print("Skipping test (exiting with code 0) since LM Studio model is required.")
                sys.exit(0)
                
            # Prioritize any model containing 'coder' or '1.5b' in its ID
            model_id = None
            for model in data:
                m_id = model.get("id", "")
                if "coder" in m_id.lower() or "1.5b" in m_id.lower():
                    model_id = m_id
                    break
            
            if not model_id:
                model_id = data[0].get("id")
                
            print(f"[LM STUDIO] Active model selected: {model_id}")
    except Exception as e:
        print(f"\n[SKIP] Failed to retrieve loaded models: {e}")
        print("Skipping test (exiting with code 0) since LM Studio is required.")
        sys.exit(0)

    # 3. Initialize Standalone Execution Sandbox (Local-Only)
    print("\n[STEP 3] Initializing Standalone Local Pipeline Sandbox...")
    
    # Initialize basic configuration
    ConfigManager.load_config()
    
    # Decoupled EventBus
    event_bus = EventBus()
    
    # Local-Only Tool Registry (sync_cloud=False blocks Supabase sync overhead)
    registry = ToolRegistry(event_bus=event_bus)
    registry.discover_all(sync_cloud=False)
    print(f"[SANDBOX] Tool registry initialized with {registry.get_tool_count()} local tools.")

    # In-memory clean memory engine
    memory = MemoryEngine()
    
    # Dynamic Trace Capturing variables
    captured_thoughts = []
    tool_calls_attempted = []
    response_tokens = []
    
    # Setup callbacks for live tracing
    async def on_tool_start(name, params, action_msg, tool_id=None):
        print(f"\n  >>> TOOL CALL STARTED: {name}({params})")
        tool_calls_attempted.append({
            "name": name,
            "arguments": params,
            "result": None
        })
        
    async def on_tool_end(name, result, tool_id=None):
        print(f"  <<< TOOL CALL COMPLETED: {name} (success={result.get('success', True)})")
        if tool_calls_attempted and tool_calls_attempted[-1]["name"] == name:
            tool_calls_attempted[-1]["result"] = result

    async def on_thinking(thought_text, type="thought", append=True, agent_name=None):
        # Captures streaming model thoughts/reasoning
        if thought_text:
            cleaned = thought_text.strip()
            if cleaned and cleaned != "...":
                captured_thoughts.append(thought_text)
                print(thought_text, end="", flush=True)

    async def on_token(token):
        # Captures streaming final generation tokens
        if token:
            response_tokens.append(token)
            print(token, end="", flush=True)

    # Standalone Local Executor
    executor = LocalToolExecutor(
        tool_registry=registry,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
        memory_engine=memory
    )

    # LiteLLM Local Adapter configured for LM Studio OpenAI compatibility endpoint
    adapter = LiteLLMAdapter(
        model_name=f"openai/{model_id}",
        base_url=LM_STUDIO_URL,
        api_key="lm-studio"
    )

    # Instantiate single-user Local Client
    client = TextModeClient(
        adapter=adapter,
        tool_registry=registry,
        tool_executor=executor,
        memory_engine=memory,
        on_thinking=on_thinking,
        on_token=on_token
    )

    # Dynamic Tool Overrides: Inject hubspace_control and search_gmail for the test turns
    original_get_tools = client.turn_coordinator._get_optimized_tools
    async def patched_get_tools(user_input, *args, **kwargs):
        schemas = await original_get_tools(user_input, *args, **kwargs)
        for t_name in ["hubspace_control", "search_gmail"]:
            if not any(s.get("name") == t_name for s in schemas):
                tool = registry._tools.get(t_name)
                if tool:
                    schemas.append(registry._extract_schema(t_name, tool))
        return schemas
    client.turn_coordinator._get_optimized_tools = patched_get_tools

    # 4. Execute Multi-Phase E2E Pipeline Verification (3 Tool turns)
    phases_to_run = [
        {
            "phase": 1,
            "name": "Turn On Lamp",
            "query": "Turn on the lamp."
        },
        {
            "phase": 2,
            "name": "Turn Off Lamp",
            "query": "Now turn off that lamp."
        },
        {
            "phase": 3,
            "name": "Check Gmail",
            "query": "Check my gmail inbox for recent messages."
        }
    ]
    
    phases_results = []
    has_failed = False
    
    for phase_cfg in phases_to_run:
        p_num = phase_cfg["phase"]
        p_name = phase_cfg["name"]
        p_query = phase_cfg["query"]
        
        print(f"\n[STEP 4.{p_num}] Executing Phase {p_num}: {p_name}...")
        print(f"  User Query: \"{p_query}\"")
        print("-" * 70)
        print("  Captured Thoughts/Reasoning:")
        print("  ", end="")
        
        # Reset trace capturing buffers for the new phase
        captured_thoughts.clear()
        tool_calls_attempted.clear()
        response_tokens.clear()
        
        start_time = time.time()
        try:
            response = await client.send_message(
                text=p_query,
                on_token=on_token
            )
            latency = time.time() - start_time
            print("\n" + "-" * 70)
            
            final_text = response.get("response") or response.get("text") or "".join(response_tokens)
            
            phases_results.append({
                "phase": p_num,
                "name": p_name,
                "user_query": p_query,
                "captured_thoughts": "".join(captured_thoughts),
                "tool_calls_attempted": list(tool_calls_attempted),
                "final_response": final_text.strip(),
                "metrics": {
                    "latency_seconds": round(latency, 2),
                    "token_count": len(response_tokens) if response_tokens else int(len(final_text) / 4)
                }
            })
            
            print(f"[SUCCESS] Phase {p_num} complete. Latency: {round(latency, 2)}s, Tool Calls: {len(tool_calls_attempted)}")
            
        except Exception as e:
            latency = time.time() - start_time
            print(f"\n[CRITICAL ERROR] Phase {p_num} failed: {e}")
            phases_results.append({
                "phase": p_num,
                "name": p_name,
                "user_query": p_query,
                "error_message": str(e),
                "metrics": {
                    "latency_seconds": round(latency, 2)
                }
            })
            has_failed = True
            break
            
    print("\n[STEP 5] Recording E2E multi-phase execution trace...")
    data_dir = Path("tests/data")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    run_output = {
        "status": "failure" if has_failed else "success",
        "model_id": model_id,
        "phases": phases_results
    }
    
    output_file = data_dir / "e2e_run_output.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(run_output, f, indent=2)
        
    print(f"[SANDBOX] E2E multi-phase execution trace successfully dumped to {output_file}")
    
    print("\n" + "="*70)
    print("=== AUTOMATED PIPELINE RUN COMPLETE ===")
    print(f"  Active Model: {model_id}")
    print(f"  Phases Run:   {len(phases_results)}")
    for r in phases_results:
        p_num = r["phase"]
        p_name = r["name"]
        p_stat = "FAIL" if "error_message" in r else "PASS"
        p_lat = r["metrics"]["latency_seconds"]
        print(f"    Phase {p_num}: {p_name} -> {p_stat} ({p_lat}s)")
    print("="*70 + "\n")
    
    if has_failed:
        sys.exit(1)

if __name__ == "__main__":
    # Ensure standard Windows selector policy is used for asyncio compatibility
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
