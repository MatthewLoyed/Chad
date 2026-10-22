
import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.tools.workspace.gmail import SearchGmailTool
from src.shared.logger import setup_logger

logger = setup_logger("Diagnostic")

async def check():
    print("\n--- GMAIL TOOL DIAGNOSTIC ---")
    
    # 1. Check for credentials
    project_root = Path(__file__).parent.parent.parent
    creds_path = project_root / "config" / "credentials.json"
    token_path = project_root / "config" / "token.json"
    
    print(f"Credentials exist: {creds_path.exists()} ({creds_path})")
    print(f"Token exists: {token_path.exists()} ({token_path})")
    
    # 2. Try executing tool
    tool = SearchGmailTool()
    print("Executing SearchGmailTool...")
    result = await tool.execute(limit=1, unread_only=True)
    
    if result.get("success"):
        print("✅ SUCCESS: Found emails!")
        print(f"Summary: {result.get('result')}")
    else:
        print("❌ FAILURE")
        print(f"Error: {result.get('error')}")
        
    print("-----------------------------\n")

if __name__ == "__main__":
    asyncio.run(check())
