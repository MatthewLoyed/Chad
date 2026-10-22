import os
import subprocess
import time
import sys
import psutil

def kill_server():
    """Find and kill any process running src/remote/server.py"""
    print("[*] Searching for running Brain Server...")
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and any('src/remote/server.py' in arg.replace('\\', '/') for arg in cmdline):
                print(f"[!] Killing Brain Server (PID: {proc.info['pid']})")
                proc.terminate()
                proc.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            continue

def start_server():
    """Start the Brain Server in a new background process."""
    print("[*] Starting Brain Server...")
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    # Use subprocess.Popen to start it in the background
    # On Windows, we use CREATE_NEW_CONSOLE if we want it visible, or just let it run.
    # For Antigravity, we'll just run it and let the log file capture output.
    with open("docs/logs/server_startup.log", "w") as f:
        subprocess.Popen(
            [sys.executable, "src/remote/server.py"],
            env=env,
            stdout=f,
            stderr=f,
            cwd=os.getcwd(),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
        )
    
    print("[+] Brain Server started in background. Logs: docs/logs/server_startup.log")
    # Give it a moment to initialize
    time.sleep(5)

if __name__ == "__main__":
    kill_server()
    start_server()
