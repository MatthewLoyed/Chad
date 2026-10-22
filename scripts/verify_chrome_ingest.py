"""
Verify Chrome Ingestion
Dry-run script to validate local extraction from Chrome's History SQLite database.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

# Set path to project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.intelligence.ingestion.chrome import ChromeHistoryIngestor

def main():
    print("=" * 50)
    print("    CHROME HISTORY INGESTOR DRY-RUN")
    print("=" * 50)
    
    # Initialize
    ingestor = ChromeHistoryIngestor()
    
    print("[*] Checking target SQLite path: {ingestor.history_path}")
    if not os.path.exists(ingestor.history_path):
        print(f"[!] CRITICAL ERROR: File does not exist at '{ingestor.history_path}'. Check user profile.")
        return
    else:
        print(f"[+] Target SQLite database located! (Size: {os.path.getsize(ingestor.history_path) / 1024 / 1024:.2f} MB)")
        
    # Set sync horizon
    sync_horizon = datetime.now(timezone.utc) - timedelta(hours=12)
    print(f"[*] Sync window set to: Last 12 hours (since {sync_horizon.strftime('%Y-%m-%d %H:%M:%S UTC')})")
    
    print("\n[*] Executing extraction routine...")
    try:
        results = ingestor.ingest(last_sync=sync_horizon)
        
        print(f"\n[+] EXTRACTION SUCCESS! Retrieved {len(results)} records.")
        
        if results:
            print("\n--- RECENT ACTIVITY SAMPLE (Max 10) ---")
            for i, entry in enumerate(results[:10]):
                safe_content = entry['content'].encode('ascii', errors='ignore').decode('ascii')
                print(f"\n[{i+1}] '{safe_content}'")
                print(f"    Timestamp:  {entry['metadata']['timestamp']}")
                print(f"    Type:       {entry['metadata']['type']}")
                print(f"    Domain:     {entry['metadata']['domain']}")
                print(f"    Hash Tag:    {entry['metadata']['hash'][:16]}...")
            print("\n" + "-" * 40)
        else:
            print("[!] Warning: Zero new entries found in the sync window. Try visiting a web page first.")
            
    except Exception as e:
        print(f"\n[CRITICAL FAIL] Ingestion handler threw error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
