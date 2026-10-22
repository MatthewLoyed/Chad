"""
Verify Supabase & Embedding Integration
Validates end-to-end vector creation, new model usage, metadata insertion, and JSONB deduplication.
"""

import os
import sys
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set path to project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.intelligence.memory_engine import MemoryEngine

def main():
    print("=" * 60)
    print(" INTEGRATION TEST: SUPABASE & EMBEDDINGS-V2")
    print("=" * 60)
    
    # Ensure API Keys are present in env
    if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        print("[!] Error: No Gemini API key found in environment. Exiting.")
        return

    try:
        # 1. Initialize Engine
        engine = MemoryEngine()
        print("[+] Connected to MemoryEngine and Repository.")
        
        # Generate isolated test components
        test_token = str(uuid.uuid4())[:8]
        test_content = f"[INTEGRATION TEST] Unified Personal Search system validation. Run Token: {test_token}."
        test_hash = f"integration_test_hash_{test_token}"
        
        test_metadata = {
            "source": "test_suite",
            "type": "integration_verify",
            "hash": test_hash,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # 2. Test Gemini-Embedding-2-Preview Call
        print("\n[*] Step 1: Invoking gemini-embedding-2-preview (768-dims)...")
        embedding = engine.get_embedding(test_content)
        if embedding:
            dim_size = len(embedding)
            print(f"[+] Vector generation successful! Dimensions: {dim_size}")
            if dim_size != 768:
                print(f"[-] Dimensionality Warning: Expected 768, got {dim_size}.")
        else:
            print("[-] ERROR: Vector embedding returned None.")
            return

        # 3. Test Before State (Deduplication check)
        print(f"\n[*] Step 2: Verifying item does not exist yet (Hash: {test_hash})...")
        exists_pre = engine.repository.has_memory_with_hash(test_hash)
        print(f"[+] Exists (Pre-Save): {exists_pre}")
        if exists_pre:
            print("[-] WARNING: Hash collide! Test failed.")
            return
            
        # 4. Commit to Supabase Cloud
        print("\n[*] Step 3: Executing commit to Supabase with embedded metadata...")
        mem_id = engine.save_memory(
            content=test_content,
            category="integration_verify",
            importance=0.05,
            metadata=test_metadata
        )
        
        if mem_id == -1:
            print("[-] ERROR: Repository returned failure ID -1.")
            return
            
        print(f"[+] Save successful. Assigned database Memory ID: {mem_id}")
        
        # 5. Verify Post State (JSONB Arrow operator check)
        print("\n[*] Step 4: Querying deduplication index for committed record...")
        exists_post = engine.repository.has_memory_with_hash(test_hash)
        print(f"[+] Exists (Post-Save): {exists_post}")
        
        if exists_post:
            print("\n[SUCCESS] INTEGRATION VERIFIED: END-TO-END VECTOR SYNC PIPELINE FUNCTIONAL!")
        else:
            print("\n[FAILURE] Deduplication index returned False. Check JSONB filtering format.")
            
        # 6. Cleanup Cloud Test Data
        print("\n[*] Step 5: Performing cloud cleanup of test record...")
        deleted = engine.repository.delete_memory(mem_id)
        print(f"[+] Delete confirmed: {deleted}")
        
    except Exception as e:
        print(f"\n[CRITICAL CRASH]: {e}")
        import traceback
        traceback.print_exc()
        
    print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
