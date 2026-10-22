r"""
Automated E2E Benchmark for Multi-Domain Coursework Sync (Canvas LMS + Gemini Notebook / Browser).

Measures and evaluates:
1. Delegation / API-First: Canvas API / specialist called for coursework discovery before blind browser automation.
2. Browser action efficiency: Total browser actions <= 12 (no infinite loops or DOM thrashing).
3. No Drive detour: Downloads kept local, zero calls to Google Drive tools.
4. Clean termination: Turn finishes cleanly without unhandled exception or timeout.
5. Structured outcome reporting with clear pass/fail criteria.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Ensure repo root is on sys.path
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

MAX_ALLOWED_BROWSER_ACTIONS = 12
MAX_ALLOWED_DURATION_S = 300.0


def evaluate_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Programmatically evaluate a captured turn trace against coursework benchmark criteria."""
    tool_calls = trace.get("tool_calls", [])
    tool_results = trace.get("tool_results", [])
    response = trace.get("response") or ""
    error = trace.get("error")
    duration_s = trace.get("duration_s", 0.0)

    tool_names = [tc.get("name") for tc in tool_calls]

    # 1. Canvas API / Delegation check
    canvas_tools = [
        "delegate_to_canvas_agent",
        "get_canvas_courses",
        "get_course_files",
        "get_course_modules",
        "get_module_items",
        "download_course_file",
    ]
    canvas_called = any(name in canvas_tools for name in tool_names)

    # Check if canvas was called first or before browser actions
    canvas_first = False
    first_canvas_idx = -1
    first_browser_idx = -1
    for idx, name in enumerate(tool_names):
        if name in canvas_tools and first_canvas_idx == -1:
            first_canvas_idx = idx
        if name and (name.startswith("browser_") or name == "delegate_to_browser_agent") and first_browser_idx == -1:
            first_browser_idx = idx

    if first_canvas_idx != -1 and (first_browser_idx == -1 or first_canvas_idx < first_browser_idx):
        canvas_first = True

    # 2. Browser action count
    browser_actions = [
        name for name in tool_names
        if name and (name.startswith("browser_") or name == "delegate_to_browser_agent")
    ]
    browser_count = len(browser_actions)
    browser_budget_ok = browser_count <= MAX_ALLOWED_BROWSER_ACTIONS

    # 3. No Drive detour check
    drive_tools = [name for name in tool_names if name and "google_drive" in name.lower()]
    no_drive_detour = len(drive_tools) == 0

    # 4. Clean termination
    clean_exit = error is None and trace.get("warmup_error") is None

    # 5. Coursework delta / cross-referencing intent identified
    # Look for evidence in tool arguments, reasoning, or response
    all_text = " ".join([
        response,
        json.dumps([tc.get("params", {}) for tc in tool_calls]),
        " ".join(trace.get("reasoning_by_agent", {}).values()),
    ]).lower()

    keywords = ["wireless", "lecture", "canvas", "notebook", "pdf"]
    matched_keywords = [kw for kw in keywords if kw in all_text]
    relevance_ok = len(matched_keywords) >= 3

    # Overall pass criteria
    passed = (
        canvas_called
        and browser_budget_ok
        and no_drive_detour
        and clean_exit
        and relevance_ok
    )

    metrics = {
        "canvas_called": canvas_called,
        "canvas_first": canvas_first,
        "browser_action_count": browser_count,
        "browser_budget_ok": browser_budget_ok,
        "no_drive_detour": no_drive_detour,
        "clean_exit": clean_exit,
        "relevance_ok": relevance_ok,
        "matched_keywords": matched_keywords,
        "duration_s": duration_s,
        "total_tool_calls": len(tool_calls),
        "passed": passed,
    }

    return metrics


async def run_benchmark() -> Dict[str, Any]:
    print("=" * 70)
    print("STARTING COURSEWORK SYNC E2E BENCHMARK")
    print(f"PROMPT: {TEST_PROMPT}")
    print("=" * 70)

    t0 = time.perf_counter()
    trace = await run_turn_inprocess(TEST_PROMPT, category="agent")
    total_time = round(time.perf_counter() - t0, 1)

    metrics = evaluate_trace(trace)

    print("\n" + "=" * 70)
    print(f"BENCHMARK COMPLETED in {total_time}s")
    print(f"OVERALL RESULT: {'PASSED' if metrics['passed'] else 'FAILED'}")
    print("=" * 70)
    print(f"- Canvas API / Specialist Called: {'YES' if metrics['canvas_called'] else 'NO'}")
    print(f"- Canvas Called First:            {'YES' if metrics['canvas_first'] else 'NO'}")
    print(f"- Browser Action Count:           {metrics['browser_action_count']} (Budget <= {MAX_ALLOWED_BROWSER_ACTIONS}: {'PASS' if metrics['browser_budget_ok'] else 'FAIL'})")
    print(f"- Zero Google Drive Detours:     {'PASS' if metrics['no_drive_detour'] else 'FAIL'}")
    print(f"- Clean Exit (No crashes):        {'PASS' if metrics['clean_exit'] else 'FAIL'}")
    print(f"- Context Relevance:              {'PASS' if metrics['relevance_ok'] else 'FAIL'} (matched {metrics['matched_keywords']})")
    print(f"- Total Wall Duration:            {metrics['duration_s']}s")

    print("\n--- TOOL SEQUENCE ---")
    for i, tc in enumerate(trace.get("tool_calls", []), 1):
        print(f"[{i}] {tc.get('name')}")

    print("\n--- FINAL AGENT SUMMARY ---")
    resp = trace.get("response") or "(No response)"
    print(resp[:400] + ("..." if len(resp) > 400 else ""))

    return {
        "metrics": metrics,
        "trace": trace,
    }


@pytest.mark.asyncio
async def test_coursework_sync_benchmark_e2e():
    """Pytest wrapper for the coursework sync benchmark."""
    result = await run_benchmark()
    metrics = result["metrics"]
    assert metrics["canvas_called"], "Expected Canvas specialist or API tools to be invoked"
    assert metrics["browser_budget_ok"], f"Browser action budget exceeded: {metrics['browser_action_count']}"
    assert metrics["no_drive_detour"], "Expected zero Google Drive tool calls"
    assert metrics["clean_exit"], f"Turn finished with errors: {result['trace'].get('error')}"
    assert metrics["passed"], "Benchmark criteria failed"


if __name__ == "__main__":
    result = asyncio.run(run_benchmark())
    sys.exit(0 if result["metrics"]["passed"] else 1)
