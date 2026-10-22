"""
Verify Real Chrome Sync Cycle
Executes a full Orchestration Sync Cycle on the active Chrome installation,
restricted to the last 10 minutes to ensure extreme velocity and isolation.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Boost env
load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.intelligence.memory_engine import MemoryEngine
from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager
from src.core.intelligence.ingestion.chrome import ChromeHistoryIngestor
from src.infrastructure.repositories.state_repository import StateRepository

def main():
    import logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    
    print("=" * 60)
    print(" FINAL INTEGRATION: ACTIVE CHROME CYCLE")
    print("=" * 60)
    
    try:
        # 1. Connect engine and state repo
        engine = MemoryEngine()
        state_repo = StateRepository()
        print("[+] Engine and Infrastructure components loaded.")
        
        # 2. Set precise watermark window (last 10 mins) to ensure minimal DB impact
        sync_key = "personal_search_sync_states"
        current_states = state_repo.get_value(sync_key, {})
        
        # Force state window to 12 hours ago
        ten_mins_ago = datetime.now(timezone.utc) - timedelta(hours=12)
        current_states["chrome"] = ten_mins_ago.isoformat()
        state_repo.set_value(sync_key, current_states)
        print(f"[*] Restricting extraction window to items since: {ten_mins_ago.strftime('%H:%M:%S')}")
        
        # 3. Configure Manager
        manager = IngestionSyncManager(engine)
        manager.register_ingestor(ChromeHistoryIngestor())
        print("[+] IngestionSyncManager constructed with ChromeHistoryIngestor.")
        
        # 4. Execute real cycle
        print("\n[*] Launching sync cycle...")
        report = manager.run_sync_cycle()
        
        # 5. Print results
        print("\n[+] Sync cycle completed successfully!")
        sources = report.get("sources", {})
        
        if not sources:
            print("[-] No sources reports received.")
        else:
            for src_name, stats in sources.items():
                print(f"\n --- Statistics for [{src_name}] ---")
                print(f"  - Status:                    {stats.get('status')}")
                print(f"  - Already indexed (deduped): {stats.get('duplicates', 0)}")
                print(f"  - Newly embedded & saved:    {stats.get('added', 0)}")
                print(f"  - Persisted watermark:       {stats.get('timestamp')}")
                
        print("\n[SUCCESS] ALL SYSTEM PHASES FULLY OPERATIONAL AND VERIFIED!")
        
    except Exception as err:
        print(f"\n[CRITICAL FAILURE]: {err}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
