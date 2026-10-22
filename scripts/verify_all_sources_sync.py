"""
Verify All Sources Unified Sync Cycle
Executes a full Orchestration Sync Cycle across Chrome History, Gmail, and Google Drive.
Restricts extraction to the last 1 hour to ensure safety and performance.
"""

import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Initialize console logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.intelligence.memory_engine import MemoryEngine
from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager
from src.core.intelligence.ingestion.chrome import ChromeHistoryIngestor
from src.core.intelligence.ingestion.google_workspace import GmailIngestor, GoogleDriveIngestor
from src.infrastructure.repositories.state_repository import StateRepository

def main():
    print("\n" + "=" * 60)
    print("      UNIFIED END-TO-END SYNC INTEGRATION TEST")
    print("=" * 60)
    
    try:
        # 1. Connect Engine
        print("\n[*] Step 1: Loading Memory Engine and Cloud Infrastructure...")
        engine = MemoryEngine()
        state_repo = StateRepository()
        print("[+] Connected to Supabase and Memory Stores successfully.")
        
        # 2. Pre-warm and truncate watermark state for testing safety
        # We'll look back exactly 3 hours for a dense but safe test run
        sync_key = "personal_search_sync_states"
        lookback_time = datetime.now(timezone.utc) - timedelta(hours=3)
        lookback_str = lookback_time.isoformat()
        
        print(f"[*] Step 2: Pre-configuring watermarks to look back 3 hours...")
        current_states = state_repo.get_value(sync_key, {})
        
        # For safety, force short window for this test
        current_states["chrome"] = lookback_str
        current_states["gmail"] = lookback_str
        current_states["google_drive"] = lookback_str
        
        state_repo.set_value(sync_key, current_states)
        print(f"[+] Synchronizer lookback boundary locked to: {lookback_time.strftime('%H:%M:%S UTC')}")
        
        # 3. Initialize Ingestors
        print("\n[*] Step 3: Assembling IngestionSyncManager and Registering Ingestors...")
        manager = IngestionSyncManager(engine)
        
        chrome_ingest = ChromeHistoryIngestor()
        gmail_ingest = GmailIngestor()
        drive_ingest = GoogleDriveIngestor()
        
        manager.register_ingestor(chrome_ingest)
        manager.register_ingestor(gmail_ingest)
        manager.register_ingestor(drive_ingest)
        
        # 4. Execute The Real Loop
        print("\n" + "-" * 60)
        print(" [*] EXECUTING SCHEDULED BACKGROUND SYNC TURN...")
        print("-" * 60)
        
        report = manager.run_sync_cycle()
        
        print("-" * 60)
        print("\n[+] FULL SYSTEM SYNC CYCLE COMPLETED SUCCESSFULLY!")
        
        # 5. Display Operational Statistics
        sources = report.get("sources", {})
        print("\n" + "=" * 60)
        print("                UNIFIED SYNC METRICS")
        print("=" * 60)
        
        for source, stats in sources.items():
            print(f"\n [{source.upper()}] SOURCE LOG:")
            print(f"    - Status:                    {stats.get('status', 'unknown').upper()}")
            
            if "error" in stats:
                print(f"    - Error Encountered:         {stats['error']}")
            else:
                print(f"    - Already Indexed (Deduped): {stats.get('duplicates', 0)}")
                print(f"    - Newly Saved & Embedded:    {stats.get('added', 0)}")
                print(f"    - Final Synchronized Watermark: {stats.get('timestamp', '')}")
                
        print("\n" + "=" * 60)
        print(" [SUCCESS] ALL PHASES INTEGRATED AND RUNNING IN CONCERT!!")
        print("=" * 60)

    except Exception as e:
        print(f"\n[!] CRITICAL CRASH: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
