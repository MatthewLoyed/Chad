import sys
import os
import asyncio
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.shared.config_manager import ConfigManager
from src.application.system_factory import SystemFactory
from src.shared.context import RequestContext
from src.core.intelligence.adapters.ai_adapter_base import GenerateResponse, TokenUsage

async def test_full_pipeline_interception():
    print("\n=== COMPREHENSIVE PIPELINE LEAK TEST ===")
    
    # 1. Spin up Factory Core Engine
    print("[1] Booting core engine via SystemFactory...")
    config = ConfigManager.load_config()
    container = SystemFactory.build_core_engine(config)
    
    coordinator = container.turn_coordinator
    adapter = container.adapter
    
    print(f"[2] Active Adapter: {adapter.model_name if adapter else 'None'}")
    
    if not adapter:
        print("[-] Error: No active adapter found in the system container.")
        return

    # Capture list for messages
    captured_messages = []

    # 3. Define intercepted generate method
    async def mock_generate(messages, *args, **kwargs):
        print("\n!!! INTERCEPTED INBOUND MESSAGES AT THE ADAPTER LAYER !!!")
        captured_messages.extend(messages)
        # Return fake response to cleanly end the turn immediately
        return GenerateResponse(
            text="Simulated reply.",
            tool_calls=None,
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        )

    # Trace semantic search itself!
    original_search = container.memory_engine.semantic_search
    def mock_semantic_search(query, *args, **kwargs):
        engine = container.memory_engine
        total_memories = len(engine.get_all_memories())
        print(f"\n[RAG TRACE] MemoryEngine Session Key: '{engine.session_key}'")
        print(f"[RAG TRACE] MemoryEngine Total Memories: {total_memories}")
        print(f"[RAG TRACE] MemoryEngine.semantic_search() called with query='{query}'")
        res = original_search(query, *args, **kwargs)
        print(f"[RAG TRACE] Search returned {len(res)} memories:")
        for i, m in enumerate(res):
            content = m.get('content', 'None')
            safe_content = content.encode('ascii', errors='replace').decode('ascii')
            similarity = m.get('similarity', 0.0)
            print(f"    -> Result {i+1} (Sim: {similarity}): {safe_content[:100]}...")
        return res
    
    container.memory_engine.semantic_search = mock_semantic_search

    # 4. Apply Monkeypatch
    print("[3] Applying monkeypatch to the active adapter's .generate() method...")
    original_generate = adapter.generate
    adapter.generate = mock_generate

    # 5. Run Turn
    try:
        test_prompt = "Did I watch any videos recently about anime?"
        print(f"[4] Executing turn_coordinator.dispatch() with prompt: '{test_prompt}'")
        
        context = RequestContext(platform="pc", session_id="diag_session_001")
        
        # CRITICAL: Mirror the Orchestrator by propagating the active session ID down 
        # to the shared MemoryEngine before the turn starts. Without this, tests 
        # bypass isolation checks and fail to detect RAG leakage/blockage bugs!
        container.memory_engine.session_key = context.session_id
        
        await coordinator.dispatch(
            prompt=test_prompt,
            context=context,
            thinking_level="medium"
        )
        
        print("\n=== ANALYSIS OF CAPTURED MESSAGES ===")
        if not captured_messages:
            print("[-] FAILURE: No messages were intercepted at the adapter!")
            return

        print(f"Total intercepted messages: {len(captured_messages)}")
        
        for i, msg in enumerate(captured_messages):
            role = msg.get("role")
            content = msg.get("content", "")
            print(f"\n--- Message {i+1} [Role: {role}] ---")
            
            # Handle multimodal blocks vs pure string
            content_str = str(content)
            
            # Dump the whole thing to disk for inspection!
            if role == "system":
                dump_path = "docs/logs/diagnostic_system_message.txt"
                os.makedirs(os.path.dirname(dump_path), exist_ok=True)
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(content_str)
                print(f"    [+] DUMPED FULL SYSTEM MESSAGE TO: {dump_path}")

            preview = (content_str[:400] + " ... [TRUNCATED] ... " + content_str[-400:]) if len(content_str) > 800 else content_str
            safe_preview = preview.encode('ascii', errors='replace').decode('ascii')
            print(safe_preview)
            
            # Specific check for RAG block
            count = content_str.count("### RELEVANT RECALLED MEMORIES")
            print(f"\n -> RAG block found {count} times in this message.")
            
            # If count == 1, we must check if it is ONLY instructions or actual memories
            if count == 1:
                if "- Visited " in content_str or "- Visited Web Page" in content_str or "- Visited GitHub" in content_str:
                    print("    [+] VERDICT: This message contains the ACTUAL INJECTED MEMORIES!")
                else:
                    print("    [!] VERDICT: This message contains ONLY instruction references, NO injected memory records!")
            elif count >= 2:
                 print("    [+] VERDICT: This message contains BOTH instructions AND injected memory records!")

    except Exception as e:
        print(f"[-] Fatal exception during pipeline run: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restore
        adapter.generate = original_generate
        print("\n[5] Restored original adapter state.")

if __name__ == "__main__":
    asyncio.run(test_full_pipeline_interception())
