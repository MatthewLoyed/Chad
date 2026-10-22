import sys
import os
import asyncio
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.intelligence.memory_engine import MemoryEngine
from src.application.system_factory import SystemFactory

async def test_comparison():
    query = "Did I watch any videos recently about anime?"
    
    print("=== 1. DIRECT MEMORY ENGINE INSTANCE ===")
    engine_direct = MemoryEngine()
    emb_direct = engine_direct.get_embedding(query)
    print(f"Direct Vector Length: {len(emb_direct) if emb_direct else 'NONE'}")
    print(f"Direct Vector First 5: {emb_direct[:5] if emb_direct else 'N/A'}")
    
    res_direct = engine_direct.semantic_search(query, top_k=5)
    print(f"Direct Results Found: {len(res_direct)}")
    for i, m in enumerate(res_direct):
        sim = m.get('similarity', 0.0)
        content = m.get('content', '')[:80].encode('ascii', 'replace').decode('ascii')
        print(f"  [{i+1}] Sim: {sim:.4f} | {content}")

    print("\n=== 2. FACTORY BOOTED MEMORY ENGINE INSTANCE ===")
    config = {
        "active_profile": "default",
        "mcp_servers": {}
    }
    container = SystemFactory.build_core_engine(config)
    engine_factory = container.memory_engine
    
    emb_factory = engine_factory.get_embedding(query)
    print(f"Factory Vector Length: {len(emb_factory) if emb_factory else 'NONE'}")
    print(f"Factory Vector First 5: {emb_factory[:5] if emb_factory else 'N/A'}")
    
    # Compare embeddings directly
    if emb_direct and emb_factory:
        match = True
        for v1, v2 in zip(emb_direct[:10], emb_factory[:10]):
            if abs(v1 - v2) > 1e-5:
                match = False
                break
        print(f"VECTOR MATCH (first 10 dims): {'PASSED' if match else 'FAILED'}")

    res_factory = engine_factory.semantic_search(query, top_k=5)
    print(f"Factory Results Found: {len(res_factory)}")
    for i, m in enumerate(res_factory):
        sim = m.get('similarity', 0.0)
        content = m.get('content', '')[:80].encode('ascii', 'replace').decode('ascii')
        print(f"  [{i+1}] Sim: {sim:.4f} | {content}")

if __name__ == "__main__":
    asyncio.run(test_comparison())
