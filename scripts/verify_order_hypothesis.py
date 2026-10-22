import sys
import os
import asyncio
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.intelligence.memory_engine import MemoryEngine

async def run_test():
    engine = MemoryEngine()
    query = "Did I watch any videos recently about anime?"
    
    print("--- PASS 1: Calling semantic_search() BEFORE any other call ---")
    res1 = engine.semantic_search(query, top_k=5)
    print(f"Pass 1 Results Found: {len(res1)}")
    for i, m in enumerate(res1):
        sim = m.get('similarity', 0.0)
        content = m.get('content', '')[:80].encode('ascii', 'replace').decode('ascii')
        print(f"  [{i+1}] Sim: {sim:.4f} | {content}")
        
    print("\n--- STEP 2: Calling get_all_memories() ---")
    all_m = engine.get_all_memories()
    print(f"get_all_memories() returned {len(all_m)} items.")
    
    print("\n--- PASS 3: Calling semantic_search() AFTER get_all_memories() ---")
    res2 = engine.semantic_search(query, top_k=5)
    print(f"Pass 3 Results Found: {len(res2)}")
    for i, m in enumerate(res2):
        sim = m.get('similarity', 0.0)
        content = m.get('content', '')[:80].encode('ascii', 'replace').decode('ascii')
        print(f"  [{i+1}] Sim: {sim:.4f} | {content}")

if __name__ == "__main__":
    asyncio.run(run_test())
