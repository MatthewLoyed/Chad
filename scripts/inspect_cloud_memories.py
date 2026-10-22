import sys
import os
import asyncio
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.intelligence.memory_engine import MemoryEngine

async def test_recall():
    print("=== MEMORY ENGINE RECALL TEST ===")
    engine = MemoryEngine()
    
    # Try to query raw repository first to see all entries
    print("\n[1] Fetching all memory records from MemoryEngine...")
    try:
        memories = engine.get_all_memories()
        print(f"Found {len(memories)} total memories in engine.")
        
        # Group by content type if possible
        tags = {}
        for m in memories:
            m_type = m.get("category", "unknown")
            tags[m_type] = tags.get(m_type, 0) + 1
        print(f"Record category distribution: {tags}")
        
        print("\n[2] Printing first 5 memories:")
        for i, m in enumerate(memories[:5]):
            print(f" {i+1}. [Category: {m.get('category')}] -> {str(m.get('content'))[:100]}...")
            
    except Exception as e:
        print(f"Failed to get memories: {e}")

    # Execute a semantic search for anime
    query = "Did I watch any videos recently about anime?"
    print(f"\n[3] Executing engine.semantic_search('{query}')...")
    try:
        results = engine.semantic_search(query, top_k=5)
        print(f"Semantic search returned {len(results)} items:")
        for i, res in enumerate(results):
            content = res.get('content') or 'None'
            # Safe encoding cleanup for Windows console
            safe_content = content.encode('ascii', errors='replace').decode('ascii')
            similarity = res.get('similarity', 0.0)
            print(f"  Result {i+1}: {res.get('type', 'unknown')} (Sim: {similarity:.4f}) -> {safe_content[:150]}...")
    except Exception as e:
        print(f"Semantic search failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_recall())
