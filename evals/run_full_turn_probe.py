r"""
Full in-process turn runner for the Gemini Notebook + Canvas coursework prompt.
Executes the turn through TurnCoordinator -> Supervisor -> Specialists and logs
the full step-by-step trace.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from evals.inprocess import run_turn_inprocess

TEST_PROMPT = (
    "Go to the Gemini Notebook website to see what lecture files are currently in my wireless networks course notebook, "
    "cross-reference with the lecture files in the Canvas Wireless Networks course, "
    "download all missing lecture PDFs from Canvas, and add them to the notebook."
)

async def main():
    print("=" * 70)
    print("STARTING FULL IN-PROCESS TURN PROBE")
    print(f"PROMPT: {TEST_PROMPT}")
    print("=" * 70)

    t0 = time.perf_counter()
    trace = await run_turn_inprocess(TEST_PROMPT, category="agent")
    duration = round(time.perf_counter() - t0, 1)

    print("\n" + "=" * 70)
    print(f"TURN FINISHED in {duration}s (internal duration: {trace.get('duration_s')}s)")
    print("=" * 70)

    print("\n--- TOOL CALLS EXECUTED ---")
    tool_calls = trace.get("tool_calls", [])
    if not tool_calls:
        print("No tool calls executed.")
    for i, tc in enumerate(tool_calls, 1):
        print(f"[{i}] {tc.get('name')}:")
        params_str = json.dumps(tc.get('params', {}), indent=4)
        if len(params_str) > 300:
            params_str = params_str[:297] + "..."
        print(f"    {params_str}")

    print("\n--- TOOL RESULTS ---")
    tool_results = trace.get("tool_results", [])
    for i, tr in enumerate(tool_results, 1):
        status = "SUCCESS" if tr.get("success") else "FAILED"
        print(f"[{i}] {tr.get('name')} ({status}):")
        res_str = str(tr.get("result", ""))
        if len(res_str) > 200:
            res_str = res_str[:197] + "..."
        print(f"    {res_str}")

    print("\n--- FINAL AGENT RESPONSE ---")
    print(trace.get("response") or "(No text response)")

    if trace.get("error"):
        print(f"\n[ERROR]: {trace.get('error')}")

if __name__ == "__main__":
    asyncio.run(main())
