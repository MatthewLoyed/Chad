#!/usr/bin/env python3
"""
Standalone script to generate a Google Workspace OAuth token.
This runs the browser flow locally and prints the Base64 token for cloud deployment.
"""
import os
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[1]
sys.path.append(str(project_root))

from src.tools.workspace.manager import GoogleWorkspaceManager

def main():
    print("=== CHAD GOOGLE AUTH TOKEN GENERATOR ===")
    print("This script will open a browser window for Google Authentication.")
    print("Once authorized, it will save token.json and print the Base64 string for Render.")
    print("-" * 40)

    manager = GoogleWorkspaceManager()
    
    # Check if credentials.json exists
    creds_path = project_root / "config" / "credentials.json"
    if not creds_path.exists():
        print(f"ERROR: credentials.json not found at {creds_path}")
        print("Please download your OAuth client secrets from the Google Cloud Console.")
        sys.exit(1)

    success = manager.run_local_auth_flow()
    
    if success:
        print("\nFlow completed successfully.")
    else:
        print("\nERROR: Authentication flow failed. Check logs for details.")
        sys.exit(1)

if __name__ == "__main__":
    main()
