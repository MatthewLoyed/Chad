import os
import json
from langsmith import Client
from dotenv import load_dotenv

def list_recent_runs():
    load_dotenv()
    client = Client()
    
    project_name = os.getenv("LANGSMITH_PROJECT", "ChadAi")
    print(f"[*] Recent runs for project: {project_name}")
    
    runs = list(client.list_runs(
        project_name=project_name,
        limit=10
    ))
    
    for i, run in enumerate(runs):
        print(f"{i+1}. [{run.run_type}] {run.name} | ID: {run.id} | Status: {run.status}")

if __name__ == "__main__":
    list_recent_runs()
