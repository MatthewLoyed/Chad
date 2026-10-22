# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
"""
Execute the live workflow test for the Jordan family + finance + chart + video + email task
against the running dev Brain on port 8788.
"""
import asyncio
import os
import sys
from pathlib import Path

# Set environment
os.environ["CHAD_SERVER_URL"] = "ws://localhost:8788/ws"
os.environ["PYTHONPATH"] = "."
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import evals.live as live

async def main():
    prompt = (
        "Make a comprehensive report about my finances and family context. "
        "Include an expense/spending breakdown chart, pick a relevant educational personal finance or investing video on YouTube, "
        "and draft an email to Jordan with the summarized results. "
        "Present the final report cleanly in chat. "
        "You have full creative authority — use your own best judgment for all choices, video selection, and formatting. "
        "Do not ask clarifying questions; proceed directly to formulate the execution plan."
    )
    print("=" * 70)
    print(f"STARTING LIVE WORKFLOW ON DEV BRAIN (:8788)")
    print(f"Prompt: {prompt}")
    print("=" * 70)

    trace = await live.probe_workflow(
        prompt=prompt,
        category="planning",
        max_rounds=6,
    )

    print("\n" + "=" * 70)
    print("WORKFLOW TRACE SUMMARY")
    print("=" * 70)
    print(f"Status        : {trace.status}")
    print(f"Stop Reason   : {trace.stop_reason}")
    print(f"Rounds Run    : {trace.rounds}")
    print(f"Completed     : {trace.completed}")
    print(f"Duration (s)  : {trace.duration_s}")
    print(f"Executed Tools: {trace.tool_names}")
    if trace.error:
        print(f"Error         : {trace.error}")
    
    plan_to_show = trace.final_plan or trace.plan
    if plan_to_show:
        print("\n=== EXECUTION PLAN ===")
        for s in plan_to_show:
            status = s.get("status", "unknown").upper()
            tools_used = s.get("tools") or []
            print(f"- [{status}] {s.get('id', '')}: {s.get('description', '')} (tools: {tools_used})")

    print("\n=== TOOL CALLS ===")
    for tc in trace.tool_calls:
        print(f"-> {tc.get('name')}: {tc.get('params')}")

    print("\n=== FINAL RESPONSE ===")
    print(trace.final_response or "(No response text)")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
