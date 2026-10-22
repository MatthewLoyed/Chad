import sys
import os
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

# Configure standalone console logging for clarity
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("VerifyGoogle")

# Ensure project path is resolved
load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Verify imports work
try:
    from src.core.intelligence.ingestion.google_workspace import GmailIngestor, GoogleDriveIngestor
    from src.tools.workspace.manager import GoogleWorkspaceManager
    logger.info("Successfully imported Google ingestors and workspace infrastructure.")
except Exception as e:
    logger.error(f"Failed basic imports: {e}")
    sys.exit(1)

def run_verification():
    print("\n" + "="*60)
    print("         GOOGLE WORKSPACE EXTRACTION DIAGNOSTIC")
    print("="*60)
    
    manager = GoogleWorkspaceManager()
    
    # Step 1: Pre-verify OAuth validity
    print("\n[*] Validating Google OAuth Tokens...")
    try:
        auth_success, auth_error = manager.authenticate()
        if not auth_success:
            print(f"[!] GOOGLE AUTHENTICATION FAILED: {auth_error}")
            print("[TIP] Please run the workflow step to refresh Google tokens locally.")
            return
        print("[+] Google authentication credentials loaded and valid!")
    except Exception as ae:
        print(f"[!] Critical error during auth prewarm: {ae}")
        return

    # Define standard lookback boundary: Last 24 hours
    lookback = datetime.now(timezone.utc) - timedelta(days=1)
    print(f"[*] Synchronizer lookback window initialized to: {lookback.isoformat()}")

    # Step 2: Verify Gmail Ingestion
    print("\n" + "-"*60)
    print("[*] DRIVING GMAIL EXTRACTION ROUTINE...")
    print("-"*60)
    
    gmail = GmailIngestor()
    try:
        emails = gmail.ingest(last_sync=lookback)
        print(f"[+] Extracted {len(emails)} emails matching chronological filter.")
        
        for idx, email in enumerate(emails[:5]):
            meta = email["metadata"]
            sender = meta['sender'].encode('ascii', errors='ignore').decode('ascii')
            subject = meta['subject'].encode('ascii', errors='ignore').decode('ascii')
            snippet = email['content'].splitlines()[1][:80].encode('ascii', errors='ignore').decode('ascii')
            
            print(f"\n    [{idx+1}] {meta['timestamp']}")
            print(f"        From:    {sender}")
            print(f"        Subject: {subject}")
            print(f"        Hash:    {meta['hash']}")
            print(f"        Content: {snippet}...") # Snippet preview
            
        if not emails:
            print("    (No matching emails found in the last 24 hours)")
            
    except Exception as ge:
        print(f"[!] Gmail Extraction Failed: {ge}")

    # Step 3: Verify Google Drive Ingestion
    print("\n" + "-"*60)
    print("[*] DRIVING GOOGLE DRIVE EXTRACTION ROUTINE...")
    print("-"*60)
    
    drive = GoogleDriveIngestor()
    try:
        files = drive.ingest(last_sync=lookback)
        print(f"[+] Extracted {len(files)} modified files matching chronological filter.")
        
        for idx, file in enumerate(files[:5]):
            meta = file["metadata"]
            print(f"\n    [{idx+1}] {meta['timestamp']}")
            print(f"        Type: {meta['file_type']}")
            print(f"        Name: {meta['file_name']}")
            print(f"        Hash: {meta['hash']}")
            print(f"        URL:  {meta['url'][:60]}...")
            
        if not files:
            print("    (No modified files found in the last 24 hours)")
            
    except Exception as de:
        print(f"[!] Drive Extraction Failed: {de}")

    print("\n" + "="*60)
    print("            DIAGNOSTIC EXTRACTION COMPLETED")
    print("="*60)

if __name__ == "__main__":
    run_verification()
