import os
import json
from langsmith import Client
from dotenv import load_dotenv

def fetch_full_trace():
    load_dotenv()
    client = Client()
    
    project_name = os.getenv("LANGSMITH_PROJECT", "ChadAi")
    print(f"[*] Fetching full trace for the latest turn...")
    
    # Get the latest run that has no parent (the root)
    roots = list(client.list_runs(
        project_name=project_name,
        is_root=True,
        limit=1
    ))
    
    if not roots:
        print("[!] No root traces found.")
        return
    
    root = roots[0]
    print(f"\n--- ROOT RUN ---")
    print(f"ID: {root.id}")
    print(f"Name: {root.name}")
    print(f"Status: {root.status}")
    
    # Fetch all descendant runs
    descendants = list(client.list_runs(
        project_name=project_name,
        trace_id=root.id
    ))
    
    # Sort by start time
    descendants.sort(key=lambda x: x.start_time)
    
    print("\n--- EXECUTION FLOW ---")
    for run in descendants:
        indent = "  " * getattr(run, 'extra', {}).get('metadata', {}).get('depth', 0) # Depth isn't always there
        # Simple indentation based on nesting (not perfect but helpful)
        print(f"[{run.run_type}] {run.name} ({run.status})")

if __name__ == "__main__":
    fetch_full_trace()
