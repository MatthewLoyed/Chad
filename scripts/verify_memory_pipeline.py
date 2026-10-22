"""
Test Memory Pipeline Script
===========================
1. Instantiates the real MemoryEngine.
2. Executes semantic search on the knowledge layer using your test query.
3. Simulates the SystemPromptBuilder to construct the EXACT system instructions.
4. Dumps the generated blocks BEFORE the LLM is invoked.
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

# Path Setup
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from src.core.intelligence.memory_engine import MemoryEngine
from src.core.intelligence.prompt_builder import SystemPromptBuilder
from src.shared.config_manager import ConfigManager
from src.infrastructure.database.supabase_manager import get_supabase_manager

# Configure logging to see exactly what's happening under the hood
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d | %(message)s'
)
logger = logging.getLogger("MemoryPipelineTester")

async def main():
    print("\n" + "=" * 80)
    print(" [ DIAGNOSTIC ] CHAD KNOWLEDGE LAYER PIPELINE TESTER ")
    print("=" * 80)
    
    # Load preferences and configurations
    ConfigManager.load_config()
    
    query = "Did I watch any videos recently about anime?"
    print(f"\n[1] Testing Input Query: '{query}'")
    
    # 1. Verify Supabase connectivity and raw memories existence
    supabase = get_supabase_manager()
    if not supabase or not supabase.enabled:
        print("[!] ERROR: Supabase Manager is not enabled or credentials missing!")
        return

    print("[2] Connected to Supabase. Fetching raw statistics from 'memories' table...")
    try:
        # Quick query to count memories in DB
        res = supabase.rpc("get_memory_statistics", {})
        print(f"    -> Total database memories found: {res.get('total_memories', 'Unknown') if res else '0'}")
    except Exception as e:
        print(f"    -> FAILED to get stats: {e}")

    # 2. Initialize Memory Engine
    print("\n[3] Initializing MemoryEngine...")
    memory_engine = MemoryEngine()
    
    # Force debug logs for MemoryEngine during this test
    logging.getLogger("MemoryEngine").setLevel(logging.DEBUG)
    
    # 3. Run Semantic Recall Manually
    print("\n[4] Running Manual Semantic Search via MemoryEngine.semantic_search()...")
    try:
        # Try semantic matching
        search_results = memory_engine.semantic_search(query, top_k=5)
        print(f"    -> Semantic Search returned {len(search_results)} matches:")
        for idx, match in enumerate(search_results, 1):
            sim = match.get('similarity', 0.0)
            # Safe representation for terminal
            content_safe = str(match.get('content')).encode('ascii', 'replace').decode()
            print(f"       [{idx}] [Sim: {sim:.3f}] Category: {match.get('type')} | Content: \"{content_safe}\"")
            
        if not search_results:
            print("       [WARNING] Semantic Search returned ZERO results!")
            print("       Is Gemini API Key active? Checking keyword fallback...")
            fallback = memory_engine.search_memories(query, limit=5)
            print(f"       -> Keyword fallback returned {len(fallback)} results.")
            for idx, match in enumerate(fallback, 1):
                c_safe = str(match.get('content')).encode('ascii', 'replace').decode()
                print(f"          [{idx}] Category: {match.get('type')} | Content: \"{c_safe}\"")
    except Exception as e:
        print(f"    -> EXCEPTION during semantic search: {e}")
        import traceback
        traceback.print_exc()

    # 4. Generate Memory Block
    print("\n[5] Generating context block via MemoryEngine.get_memory_block()...")
    try:
        mem_block = memory_engine.get_memory_block(query=query)
        if mem_block:
            print("    --- MEMORY BLOCK GENERATED ---")
            # Do not dump the entire block to stdout to avoid CP1252 encoding crashes, 
            # it'll be captured in the final dump file instead.
            print(f"    (Retrieved block of length {len(mem_block)} characters)")
            print("    ------------------------------")
        else:
            print("    [!] Block is EMPTY! Memory block injection failed or bypassed.")
    except Exception as e:
        print(f"    -> EXCEPTION generating block: {e}")

    # 5. Build Full System Prompt
    print("\n[6] Building Final System Prompt via SystemPromptBuilder...")
    try:
        full_prompt = SystemPromptBuilder.build(
            base_instruction="You are CHAD.",
            memory_engine=memory_engine,
            initiator="user",
            platform="pc",
            user_input=query,
            is_conversational=False,
            uses_browser=False
        )
    except Exception as e:
        print(f"    -> EXCEPTION building system prompt: {e}")
        import traceback
        traceback.print_exc()
        full_prompt = None

    # 6. Final Result Dump
    print("\n" + "=" * 80)
    print(" === PIPELINE EXECUTION COMPLETE ===")
    print("=" * 80 + "\n")
    
    output_path = project_root / "scripts" / "pipeline_output.txt"
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("=== [ DIAGNOSTIC PIPELINE RUN ] ===\n")
            f.write(f"QUERY: {query}\n\n")
            if full_prompt:
                f.write("--- FINAL SYSTEM PROMPT ---\n")
                f.write(full_prompt)
                f.write("\n---------------------------\n")
            else:
                f.write("[!] FAILED: System prompt construction yielded None/Exception.\n")
        print(f"[SUCCESS] Wrote final system prompt payload to {output_path}")
    except Exception as e:
        print(f"[!] FAILED to write output file: {e}")

    print("\n" + "=" * 80)
    print(" [ PIPELINE STOPPED ] Stopped before invoking LLM Adapter as requested.")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    # Workaround for unicode prints crashing standard windows CLI 
    # if it still fails, we run inside a try-except and let the main dump file capture the output
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
