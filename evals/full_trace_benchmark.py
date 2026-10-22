# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
"""
Full-trace multi-round benchmark for 3 zero-ambiguity prompts on the isolated Dev Brain (port 8788).
Tests the complete end-to-end lifecycle: Planning -> Approval -> Supervisor Execution -> Deliverables.
"""

import os
import sys
import time
import json
import asyncio
from pathlib import Path

# Force UTF-8 encoding on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath("."))
os.environ["CHAD_SERVER_URL"] = "ws://localhost:8788/ws"
os.environ["LANGSMITH_LIVE"] = "0"
os.environ["CHAD_DAEMONS_ENABLED"] = "false"

from evals.live import probe_workflow, open_eval_ws, server_is_up

PROMPTS = [
    {
        "id": "prompt_1_finance_noel",
        "title": "Prompt 1: Finance Summary + Jordan Draft",
        "prompt": (
            "Make a comprehensive report about my finances and family context. "
            "Retrieve my account balances and recent transactions from the last 30 days, "
            "generate an expense spending breakdown chart, search contacts and memory for Jordan's email address, "
            "and create a Gmail draft addressed to Jordan titled 'Monthly Expense Breakdown & Family Summary' "
            "containing the itemized spending numbers and draft summary. Present the full markdown summary and chart in chat. "
            "Do not ask any clarifying questions; proceed directly to formulate and execute the plan."
        ),
        "expected_artifacts": [],
    },
    {
        "id": "prompt_2_code_audit",
        "title": "Prompt 2: Narration Guard Test Audit",
        "prompt": (
            "Perform an audit of Chad's prompt guard test suite: Inspect tests/unit/test_narration_guard.py to see what behaviors it tests. "
            "Run pytest specifically on that file using terminal execution tools. Analyze the pass/fail output and write a structured "
            "markdown report saved directly to artifacts/narration_guard_audit.md detailing the test names, execution status, and coverage summary. "
            "Display the report in chat once written. Do not ask any clarifying questions; proceed directly to formulate and execute the plan."
        ),
        "expected_artifacts": ["artifacts/narration_guard_audit.md"],
    },
    {
        "id": "prompt_3_model_research",
        "title": "Prompt 3: Model Comparison Matrix",
        "prompt": (
            "Research and compare 'Qwen 2.5 Coder 32B' and 'Llama 3.3 70B' for local agentic tool use. "
            "Use web search to gather: (1) context window limits, (2) HumanEval and SWE-bench benchmark scores, "
            "and (3) estimated VRAM required for 4-bit quantization. Synthesize the findings into a markdown comparison table "
            "with key tradeoffs, and write the final document to artifacts/local_model_comparison.md. "
            "Present the summary table in chat. Do not ask any clarifying questions; proceed directly to formulate and execute the plan."
        ),
        "expected_artifacts": ["artifacts/local_model_comparison.md"],
    },
]


async def run_full_traces():
    print("=" * 80)
    print("STARTING FULL WORKFLOW TRACES BENCHMARK (Dev Brain: port 8788)")
    print("Target Planner & Agent Model: prism-ml/bonsai-27b")
    print("=" * 80)

    # Verify server is up on 8788
    if not server_is_up():
        print("[ERROR] Dev Brain on port 8788 is not reachable!")
        sys.exit(1)
    print("[INIT] Dev Brain 8788 is UP and healthy.\n")

    results = []

    for item in PROMPTS:
        p_id = item["id"]
        title = item["title"]
        prompt_text = item["prompt"]
        expected_artifacts = item["expected_artifacts"]

        print("=" * 80)
        print(f"RUNNING FULL WORKFLOW TRACE: {title}")
        print(f"Prompt: {prompt_text}")
        print("=" * 80)

        t_start = time.time()
        try:
            # probe_workflow drives multi-turn:
            # Round 1 -> Plan proposed -> graph pauses with awaiting_approval
            # Auto-approves -> executes steps across subsequent rounds
            trace = await probe_workflow(
                prompt_text,
                category="planning",
                max_rounds=6,
            )
            duration = round(time.time() - t_start, 1)

            # Check expected artifacts
            artifact_status = {}
            for art in expected_artifacts:
                art_path = Path(art)
                exists = art_path.exists()
                size = art_path.stat().st_size if exists else 0
                artifact_status[art] = {"exists": exists, "size_bytes": size}

            print(f"\n--- TRACE SUMMARY FOR {p_id} ---")
            print(f"Duration         : {duration}s")
            print(f"Total Rounds     : {trace.rounds}")
            print(f"Completed        : {trace.completed}")
            print(f"Stop Reason      : {trace.stop_reason}")
            print(f"Tools Executed   : {trace.tool_names}")
            print(f"Artifacts Status : {artifact_status}")
            print(f"Rounds Breakdown : {json.dumps(trace.rounds_detail, indent=2)}")

            if trace.final_response:
                preview = trace.final_response[:400].replace('\n', ' ')
                print(f"Final Response Preview: {preview}...")

            record = {
                "id": p_id,
                "title": title,
                "duration_s": duration,
                "rounds": trace.rounds,
                "completed": trace.completed,
                "stop_reason": trace.stop_reason,
                "tool_names": trace.tool_names,
                "plan": trace.plan,
                "final_plan": trace.final_plan,
                "rounds_detail": trace.rounds_detail,
                "artifacts": artifact_status,
                "response_length": len(trace.final_response or ""),
                "error": trace.error,
            }
            results.append(record)

        except Exception as e:
            duration = round(time.time() - t_start, 1)
            print(f"[ERROR] Trace failed for {p_id}: {e}")
            results.append({
                "id": p_id,
                "title": title,
                "duration_s": duration,
                "error": str(e),
                "completed": False,
            })

    # Save results to artifacts
    out_dir = Path("artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "full_trace_benchmark_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 80)
    print("FULL TRACE BENCHMARK COMPLETE")
    print("=" * 80)
    for r in results:
        status_label = "[PASS]" if r.get("completed") or (r.get("artifacts") and all(v.get("exists") for v in r.get("artifacts", {}).values())) else "[WARN]"
        print(f"{status_label} {r['title']}: {r.get('rounds', 0)} rounds, {r.get('duration_s', 0)}s, tools={len(r.get('tool_names', []))}")

    print(f"\nSaved detailed trace results to: {out_path}")


if __name__ == "__main__":
    asyncio.run(run_full_traces())
