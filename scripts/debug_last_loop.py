"""
Run this after an infinite loop to pull the trace and analyze it.
Usage: python scripts/debug_last_loop.py
"""
import os
from datetime import datetime, timedelta, timezone
from langsmith import Client
from dotenv import load_dotenv

load_dotenv()

client = Client()

# Pull the most recent errored or long-running root runs from chad
project_name = os.getenv("LANGSMITH_PROJECT", "ChadAi")
print(f"Checking project: {project_name}")

runs = list(client.list_runs(
    project_name=project_name,
    is_root=True,
    start_time=datetime.now(timezone.utc) - timedelta(hours=2),
    select=["id", "name", "status", "error", "start_time", "end_time", "inputs", "outputs"],
    limit=5,
))

if not runs:
    print("No recent runs found.")
    exit()

# Take the most recent one
run = runs[0]
print(f"Run ID:     {run.id}")
print(f"Status:     {run.status}")
print(f"Error:      {run.error}")
print(f"Started:    {run.start_time}")
print(f"Input:      {str(run.inputs)[:300]}")
print()

# Pull all child spans (nodes) for this run
children = list(client.list_runs(
    project_name=project_name,
    trace_id=run.id,
    select=["id", "name", "run_type", "status", "error", "inputs", "outputs", "start_time"],
))

print(f"Total spans in trace: {len(children)}")
print()

# Group by node name to detect loops
from collections import Counter
node_counts = Counter(r.name for r in children)
print("Node execution counts (loop detector):")
for name, count in node_counts.most_common():
    flag = " ⚠️ LOOP" if count > 5 else ""
    print(f"  {name}: {count}x{flag}")

print()
print("Execution order (last 20 spans):")
sorted_children = sorted(children, key=lambda r: r.start_time or datetime.min.replace(tzinfo=timezone.utc))
for r in sorted_children[-20:]:
    err = f" ERROR: {r.error[:80]}" if r.error else ""
    print(f"  [{r.name}] {r.status}{err}")

print("\n--- PENDING RUNS CLEANUP ---")
# Close any stuck pending runs
pending = list(client.list_runs(
    project_name=project_name,
    filter='eq(status, "pending")',
    is_root=True,
    limit=50,
))
for r in pending:
    client.update_run(r.id, end_time=datetime.now(timezone.utc), error="Force-closed: cancelled by user")
    print(f"Closed pending run: {r.id}")
