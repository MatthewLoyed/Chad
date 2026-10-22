import sys
import os
import asyncio
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.application.system_factory import SystemFactory

def test_tool_selection(prompt: str):
    print(f"\n=== TOOL SELECTION DIAGNOSTIC ===")
    print(f"Testing prompt: '{prompt}'")
    
    config = {
        "active_profile": "default",
        "mcp_servers": {}
    }
    container = SystemFactory.build_core_engine(config)
    reg = container.tool_registry
    
    print("\n[1] Checking if 'manage_user_memory' is registered...")
    all_tools = list(reg.tools.keys())
    if "manage_user_memory" in all_tools:
        print("[OK] SUCCESS: 'manage_user_memory' IS registered in the tool registry!")
    else:
        print("[FAIL] FAILURE: 'manage_user_memory' IS NOT registered in the tool registry!")
        print(f"Available tools: {all_tools}")
        return

    print("\n[2] Evaluating get_schemas_for_turn()...")
    if hasattr(reg, 'get_schemas_for_turn'):
        result = reg.get_schemas_for_turn(prompt)
        schemas = result.get("schemas", [])
        is_simple = result.get("is_simple", False)
        
        print(f"Turn Complexity: is_simple={is_simple}")
        print(f"Returned {len(schemas)} tools in schema list:")
        
        found = False
        for idx, s in enumerate(schemas):
            name = s.get('name')
            desc = s.get('description', '')[:60] + "..."
            print(f"  {idx+1}. {name:<20} | {desc}")
            if name == "manage_user_memory":
                found = True

        print("\n[3] DIRECT SEMANTIC RETRIEVAL ANALYSIS")
        # Access internal selector
        selector = reg._selector
        # Query direct Supabase RAG
        print("Executing direct Supabase vector search candidates...")
        candidates = selector.retrieve_relevant_tools(prompt, reg.tools, k=20)
        
        if not candidates:
            print("⚠️ No candidates returned by SemanticToolSelector!")
        else:
            print(f"\nTop Vector-Ranked Matches (Sorted by Similarity):")
            print(f"{'Rank':<5} | {'Tool Name':<25} | {'Score':<10} | {'Source':<15}")
            print("-" * 70)
            for idx, cand in enumerate(candidates):
                name = cand.get('name', 'Unknown')
                score = cand.get('similarity', 0.0)
                source = cand.get('source', 'cloud')
                print(f"{idx+1:<5} | {name:<25} | {score:<10.5f} | {source:<15}")
                
        if found:
            print("\n[OK] SUCCESS: 'manage_user_memory' was correctly selected for this prompt!")
        else:
            print("\n[FAIL] FAILURE: 'manage_user_memory' was NOT selected for this prompt!")
    else:
        print("[WARN] Warning: registry does not have get_schemas_for_turn!")

if __name__ == "__main__":
    test_prompt = "remember that i like reeses"
    test_tool_selection(test_prompt)
