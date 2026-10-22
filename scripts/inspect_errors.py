import os
import json
from langsmith import Client
from dotenv import load_dotenv

def inspect_failed_runs():
    load_dotenv()
    client = Client()
    
    project_name = os.getenv("LANGSMITH_PROJECT", "ChadAi")
    print(f"[*] Inspecting failed runs for project: {project_name}")
    
    runs = list(client.list_runs(
        project_name=project_name,
        status="error",
        limit=5
    ))
    
    for i, run in enumerate(runs):
        print(f"\n--- FAILED RUN {i+1} ---")
        print(f"Name: {run.name}")
        print(f"ID: {run.id}")
        print(f"Error: {run.error}")

if __name__ == "__main__":
    inspect_failed_runs()
