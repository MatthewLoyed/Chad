import os
import sys
import time
import asyncio
import json
from dotenv import load_dotenv

# Pre-req: Add working directory to sys path
sys.path.append(os.getcwd())

from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository

async def run_diagnostic():
    print("="*60)
    print(" DIAGNOSTIC: Supabase Latency Bench")
    print("="*60)
    
    load_dotenv()
    
    # Step 1: SupabaseManager instantiation
    start = time.perf_counter()
    mgr = get_supabase_manager()
    elapsed = time.perf_counter() - start
    print(f"[1/4] Get Supabase Manager: {elapsed:.4f}s")
    
    if not mgr.enabled:
        print("[ ERROR ]: Supabase is not enabled or configured!")
        return
        
    # Step 2: Repository instantiation
    start = time.perf_counter()
    repo = ChatHistoryRepository()
    elapsed = time.perf_counter() - start
    print(f"[2/4] Get ChatHistoryRepository: {elapsed:.4f}s")
    
    # Step 3: Warmup call (Raw query check)
    print("[3/4] Warmup call to Supabase (Query 'conversations' raw sync)...")
    start = time.perf_counter()
    # We run the query synchronously via mgr.query directly to check HTTP baseline
    try:
        raw_res = mgr.query("conversations", limit=1)
        elapsed = time.perf_counter() - start
        print(f"      SUCCESS: Query took {elapsed:.4f}s")
    except Exception as e:
        print(f"      FAILED: {e}")
    
    # Step 4: The actual async loop test (Exactly what the GUI executes)
    print("[4/4] Executing full async GetConversations call via Repo...")
    start = time.perf_counter()
    try:
        res = await repo.get_conversations(limit=50, offset=0)
        elapsed = time.perf_counter() - start
        print(f"      SUCCESS: Repo fetch returned {len(res)} rows in {elapsed:.4f}s")
    except Exception as e:
        print(f"      FAILED: {e}")
        
    print("="*60)
    print(" DONE.")

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
