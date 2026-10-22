import sys
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.intelligence.prompt_builder import SystemPromptBuilder
from src.core.intelligence.memory_engine import MemoryEngine

async def test_pipeline():
    print("\n=== PIPELINE DIAGNOSTIC TEST ===")
    
    # 1. Initialize Memory Engine
    print("[1] Initializing MemoryEngine...")
    engine = MemoryEngine()
    
    # 2. Simulate user prompt
    test_prompt = "Did I watch any videos recently about anime?"
    print(f"[2] Simulated user prompt: '{test_prompt}'")
    
    # 3. Get memories directly from engine first to see if they return
    print("[3] Querying memories directly via engine.get_memory_block()...")
    try:
        mem_block = engine.get_memory_block(query=test_prompt)
        print("\n--- RAW MEMORY BLOCK RETURNED ---")
        print(mem_block if mem_block else "⚠️ EMPTY BLOCK RETURNED!")
        print("--------------------------------\n")
    except Exception as e:
        print(f"⚠️ Error calling get_memory_block: {e}")
        return
        
    # 4. Simulate SystemPromptBuilder.build()
    print("[4] Running SystemPromptBuilder.build() as turn_coordinator does...")
    try:
        sys_instr = SystemPromptBuilder.build(
            base_instruction="", 
            memory_engine=engine, 
            initiator="user", 
            platform="pc",
            user_input=test_prompt,
            is_conversational=False,
            uses_browser=False
        )
        
        print("\n--- SYSTEM INSTRUCTION PREVIEW (First 500 chars) ---")
        safe_preview = sys_instr[:500].encode('ascii', errors='replace').decode('ascii')
        print(safe_preview)
        print("...\n")
        
        print("--- SYSTEM INSTRUCTION TAIL (Last 1000 chars) ---")
        safe_tail = sys_instr[-1000:].encode('ascii', errors='replace').decode('ascii')
        print(safe_tail)
        print("...\n")
        
        print("--- MEMORY BLOCK COUNT CHECK ---")
        count = sys_instr.count("### RELEVANT RECALLED MEMORIES")
        print(f"Number of times '### RELEVANT RECALLED MEMORIES' appears in sys_instr: {count}")
        
        if count >= 2:
            print("✅ SUCCESS: The memory block was injected in BOTH the instructions and the dynamic block!")
        elif count == 1:
            print("⚠️ PARTIAL: Found it once. Let's check if it has bullet points below it.")
        else:
            print("❌ FAILURE: Not found anywhere!")
            
    except Exception as e:
        print(f"Error building prompt: {e}")

if __name__ == "__main__":
    asyncio.run(test_pipeline())
