# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
"""
Benchmark the Planner phase across 3 distinct, zero-ambiguity prompts against dev Brain (:8788).
Stops immediately upon plan proposal (stop_reason == 'awaiting_approval') so plans can be audited
before executing.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Force target dev Brain
os.environ["CHAD_SERVER_URL"] = "ws://localhost:8788/ws"
os.environ["PYTHONPATH"] = "."
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import evals.live as live

PROMPTS = [
    {
        "id": "prompt_1_finance_noel",
        "title": "Prompt 1: Finance Summary + Jordan Draft",
        "prompt": (
            "Make a comprehensive report about my finances and family context. "
            "Retrieve my account balances and recent transactions from the last 30 days, "
            "generate an expense spending breakdown chart, "
            "search contacts and memory for Jordan's email address, "
            "and create a Gmail draft addressed to Jordan titled 'Monthly Expense Breakdown & Family Summary' "
            "containing the itemized spending numbers and draft summary. "
            "Present the full markdown summary and chart in chat. "
            "Do not ask any clarifying questions; proceed directly to formulate and execute the plan."
        )
    },
    {
        "id": "prompt_2_code_audit",
        "title": "Prompt 2: Narration Guard Test Audit",
        "prompt": (
            "Perform an audit of Chad's prompt guard test suite: "
            "Inspect tests/unit/test_narration_guard.py to see what behaviors it tests. "
            "Run pytest specifically on that file using terminal execution tools. "
            "Analyze the pass/fail output and write a structured markdown report saved directly to "
            "artifacts/narration_guard_audit.md detailing the test names, execution status, and coverage summary. "
            "Display the report in chat once written. "
            "Do not ask any clarifying questions; proceed directly to formulate and execute the plan."
        )
    },
    {
        "id": "prompt_3_model_research",
        "title": "Prompt 3: Model Comparison Matrix",
        "prompt": (
            "Research and compare 'Qwen 2.5 Coder 32B' and 'Llama 3.3 70B' for local agentic tool use. "
            "Use web search to gather: (1) context window limits, (2) HumanEval and SWE-bench benchmark scores, "
            "and (3) estimated VRAM required for 4-bit quantization. "
            "Synthesize the findings into a markdown comparison table with key tradeoffs, "
            "and write the final document to artifacts/local_model_comparison.md. "
            "Present the summary table in chat. "
            "Do not ask any clarifying questions; proceed directly to formulate and execute the plan."
        )
    }
]

async def run_single_plan_probe(p_info: dict, max_timeout_s: float = 240.0) -> dict:
    prompt = p_info["prompt"]
    title = p_info["title"]
    p_id = p_info["id"]

    print("\n" + "=" * 80)
    print(f"RUNNING PLANNER PHASE: {title}")
    print(f"Prompt: {prompt}")
    print("=" * 80)

    t0 = time.time()
    try:
        # probe_live drives a SINGLE turn. Category="planning" routes to scout -> planner -> awaiting_approval
        trace = await asyncio.wait_for(
            live.probe_live(
                prompt=prompt,
                category="planning",
            ),
            timeout=max_timeout_s
        )
    except asyncio.TimeoutError:
        duration = round(time.time() - t0, 1)
        print(f"[TIMEOUT] Probe exceeded {max_timeout_s}s watchdog!")
        return {
            "id": p_id,
            "title": title,
            "success": False,
            "error": f"Timed out after {max_timeout_s}s",
            "duration_s": duration,
            "stop_reason": "timeout",
            "plan": None,
            "tools": [],
            "reasoning_tokens": 0,
        }
    except Exception as e:
        duration = round(time.time() - t0, 1)
        print(f"[ERROR] Exception during probe: {e}")
        return {
            "id": p_id,
            "title": title,
            "success": False,
            "error": str(e),
            "duration_s": duration,
            "stop_reason": "error",
            "plan": None,
            "tools": [],
            "reasoning_tokens": 0,
        }

    duration = round(time.time() - t0, 1)
    plan = trace.plan
    stop_reason = trace.stop_reason
    is_awaiting_approval = (stop_reason == "awaiting_approval")
    has_plan = bool(plan and len(plan) > 0)
    success = is_awaiting_approval and has_plan

    print(f"\n--- RESULTS FOR {p_id} ---")
    print(f"Duration        : {duration}s")
    print(f"Stop Reason     : {stop_reason}")
    print(f"Plan Proposed   : {has_plan} ({len(plan) if plan else 0} steps)")
    print(f"Awaiting Apprvl : {is_awaiting_approval}")
    print(f"Executed Tools  : {trace.tool_names}")
    if trace.pending_approval_tool:
        print(f"Pending Tool    : {trace.pending_approval_tool} (Token: {trace.pending_approval_token})")
        if trace.pending_approval_prompt:
            print(f"Pending Prompt  : {trace.pending_approval_prompt}")

    if trace.gather_brief:
        print(f"\n[SCOUT GATHER BRIEF]\n{trace.gather_brief}")

    if plan:
        print("\n[PROPOSED EXECUTION PLAN]")
        for s in plan:
            print(f"  [{s.get('status', 'pending').upper()}] {s.get('id')}: {s.get('description')}")
            print(f"         Tools assigned: {s.get('tools')}")

    # Reasoning preview
    total_reasoning_chars = len(trace.reasoning_full)
    print(f"\nReasoning Length: {total_reasoning_chars} chars (~{total_reasoning_chars // 4} tokens)")
    if trace.reasoning_full:
        preview = trace.reasoning_full[:300].strip()
        print(f"Reasoning snippet: {preview}...")

    return {
        "id": p_id,
        "title": title,
        "success": success,
        "duration_s": duration,
        "stop_reason": stop_reason,
        "plan": plan,
        "gather_brief": trace.gather_brief,
        "tools": trace.tool_names,
        "reasoning_tokens": total_reasoning_chars // 4,
        "error": trace.error,
    }

async def main():
    results = []
    for p_info in PROMPTS:
        res = await run_single_plan_probe(p_info, max_timeout_s=300.0)
        results.append(res)
        # Brief pause between test runs
        await asyncio.sleep(2)

    print("\n" + "=" * 80)
    print("PLANNING PHASE BENCHMARK SUMMARY")
    print("=" * 80)
    all_passed = True
    for r in results:
        status_str = "PASS" if r["success"] else "FAIL"
        if not r["success"]:
            all_passed = False
        steps_count = len(r["plan"]) if r["plan"] else 0
        print(f"[{status_str}] {r['title']}: {steps_count} steps proposed in {r['duration_s']}s (reasoning: ~{r['reasoning_tokens']} tok, stop={r['stop_reason']})")
        if r.get("error"):
            print(f"       Error: {r['error']}")

    # Save summary json
    out_path = Path("artifacts/plan_phase_benchmark_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nDetailed results saved to {out_path}")

    if all_passed:
        print("\n>>> ALL 3 PROMPTS PROPOSED VALID PLANS! READY FOR EXECUTION! <<<")
    else:
        print("\n>>> ONE OR MORE PROMPTS DID NOT PRODUCE A VALID PLAN! <<<")

if __name__ == "__main__":
    asyncio.run(main())
