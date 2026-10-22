import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from e2b_code_interpreter import Sandbox

# Load .env
load_dotenv()

def verify_e2b():
    print("Checking E2B_API_KEY...")
    api_key = os.getenv("E2B_API_KEY")
    
    if not api_key or api_key == "PASTE_YOUR_E2B_API_KEY_HERE":
        print("❌ E2B_API_KEY is missing or still set to placeholder.")
        return False
    
    print(f"API Key found (length: {len(api_key)}). Attempting to spawn sandbox...")
    
    try:
        # Create a sandbox (reads E2B_API_KEY from env)
        sb = Sandbox.create()
        print("✅ Sandbox spawned successfully!")
        
        print("Running test Python code...")
        execution = sb.run_code("print('Hello from E2B Sandbox!')")
        
        if execution.logs.stdout:
            print(f"✅ Sandbox stdout: {execution.logs.stdout[0]}")
        
        print("Testing file system...")
        sb.files.write("test.txt", "E2B is working!")
        content = sb.files.read("test.txt")
        print(f"✅ File readback: {content}")
        
        # Close sandbox
        sb.close()
        print("\n🎉 E2B integration is FULLY VERIFIED!")
        return True
        
    except Exception as e:
        print(f"❌ E2B Verification failed: {e}")
        return False

if __name__ == "__main__":
    verify_e2b()
