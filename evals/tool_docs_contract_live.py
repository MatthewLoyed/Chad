r"""
Live tool-docs-as-contracts probe (issue #517).

Exercises the REAL LLMToolSelector.select() call against the REAL 9B model
(configured via tools.llm_selector_model, default qwen/qwen3.5-9b) over the
full local tool catalog — no RAG shortlist, no server, no Supabase writes.

This is the "first pick" mechanism the issue is about: given a query and the
full catalog of tool name -> (capped 240-char) description, does the 9B model
choose the right tool(s) on the first try? A bad first pick means the
supervisor either calls the wrong tool (wasted round-trip) or has to retry.

Cases specifically target known ambiguous/overlapping tool clusters
(gmail read-tool aliases, control_volume vs system_settings, open_browser vs
web_search) plus a spread of unambiguous single-tool queries as a control
group, so a regression in the control group would flag an edit that helped
one cluster but hurt another.

Usage:
  $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.tool_docs_contract_live
  $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.tool_docs_contract_live --repeat 3
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from typing import Any, Dict, List

# Cases: expect_any = pass if selector picked >=1 of these AND picked <= max_tools
# total AND did not pick any of forbid (a wrong/duplicate tool in the same cluster).
CASES: List[Dict[str, Any]] = [
    {
        "id": "gmail_check_inbox",
        "query": "check my emails for anything from my advisor",
        "expect_any": ["search_gmail"],
        "forbid": [],
        "max_tools": 3,
    },
    {
        "id": "gmail_read_full_body",
        "query": "read the full text of that last email from Sarah",
        "expect_any": ["read_gmail", "search_gmail"],
        "max_tools": 3,
    },
    {
        "id": "gmail_thread",
        "query": "show me the whole conversation thread with the scholarship office",
        "expect_any": ["read_gmail", "search_gmail"],
        "forbid": [],
        "max_tools": 3,
    },
    {
        "id": "volume_increase",
        "query": "turn the volume up a bit",
        "expect_any": ["control_volume"],
        "forbid": ["system_settings"],
        "max_tools": 2,
    },
    {
        "id": "volume_mute",
        "query": "mute the sound right now",
        "expect_any": ["control_volume"],
        "forbid": ["system_settings"],
        "max_tools": 2,
    },
    {
        "id": "brightness_set",
        "query": "set my screen brightness to 40 percent",
        "expect_any": ["system_settings"],
        "forbid": ["control_volume"],
        "max_tools": 2,
    },
    {
        "id": "open_website",
        "query": "open reddit in my browser",
        "expect_any": ["open_browser"],
        "forbid": ["web_search"],
        "max_tools": 2,
    },
    {
        "id": "research_question",
        "query": "what's the current weather forecast for Huntsville Alabama this weekend",
        "expect_any": ["web_search"],
        "forbid": ["open_browser"],
        "max_tools": 2,
    },
    # Control group — unambiguous single-tool queries, should already pass.
    {
        "id": "smart_home_lights",
        "query": "turn on the study desk lamp",
        "expect_any": ["hubspace_control"],
        "max_tools": 2,
    },
    {
        "id": "screenshot",
        "query": "take a screenshot of my screen",
        "expect_any": ["capture_screen"],
        "max_tools": 2,
    },
    {
        "id": "datetime",
        "query": "what time is it right now",
        "expect_any": ["get_datetime"],
        "max_tools": 2,
    },
    {
        "id": "reminder",
        "query": "remind me in 20 minutes to check the oven",
        "expect_any": ["set_reminder"],
        "max_tools": 2,
    },
    # Issue #537 — descriptions whose disambiguation clause was previously
    # dropped by the 240-char truncation cap.
    {
        "id": "presentation_vs_markdown_slides",
        "query": "turn these bullet points into a quick slide deck: intro, problem, solution, roadmap",
        "expect_any": ["render_marp", "load_skill"],
        "forbid": ["generate_presentation"],
        "max_tools": 3,
    },
    {
        "id": "canvas_all_classes_todo",
        "query": "what homework do I have due across all my classes this week",
        "expect_any": ["get_canvas_dashboard_todos"],
        "forbid": ["get_upcoming_assignments"],
        "max_tools": 3,
    },
    {
        "id": "skill_not_web_search",
        "query": "quick fact check: what year did the Berlin Wall fall",
        "expect_any": ["web_search"],
        "forbid": ["load_skill"],
        "max_tools": 3,
    },
    {
        "id": "saved_routine_not_os_macro",
        "query": "run my morning routine I saved last week",
        "expect_any": ["run_saved_routine"],
        "forbid": ["run_routine"],
        "max_tools": 3,
    },
]


def run(repeat: int) -> int:
    from src.infrastructure.tools.registry import ToolRegistry

    print("Loading tool registry + discovery...")
    registry = ToolRegistry()
    registry.discover_all()
    selector = registry._llm_selector
    print(f"Catalog size: {registry.get_tool_count()} tools")

    if not selector.is_available():
        print(f"FAIL: selector unavailable ({selector.last_unavailable_reason})")
        return 1

    model = selector._resolved_model_id or selector._model_id
    print(f"Selector model: {model}\n")

    tools = registry._tools

    totals = {"pass": 0, "fail": 0}
    per_case_pass_rates: Dict[str, List[bool]] = {c["id"]: [] for c in CASES}
    latencies: List[float] = []

    for run_idx in range(repeat):
        print(f"--- Run {run_idx + 1}/{repeat} ---")
        for case in CASES:
            t0 = time.perf_counter()
            selected = selector.select(case["query"], tools) or []
            latency = time.perf_counter() - t0
            latencies.append(latency)

            expect_any = set(case.get("expect_any", []))
            forbid = set(case.get("forbid", []))
            max_tools = case.get("max_tools", 999)

            hit_expected = bool(expect_any & set(selected))
            hit_forbidden = bool(forbid & set(selected))
            within_budget = len(selected) <= max_tools

            ok = hit_expected and not hit_forbidden and within_budget
            per_case_pass_rates[case["id"]].append(ok)
            totals["pass" if ok else "fail"] += 1

            status = "PASS" if ok else "FAIL"
            reason = ""
            if not hit_expected:
                reason += " missing-expected"
            if hit_forbidden:
                reason += " picked-forbidden"
            if not within_budget:
                reason += f" over-budget({len(selected)}>{max_tools})"

            print(
                f"  [{status}] {case['id']}: {case['query']!r}\n"
                f"         selected={selected} ({latency:.1f}s){reason}"
            )

    total_cases = totals["pass"] + totals["fail"]
    pass_rate = totals["pass"] / total_cases if total_cases else 0.0
    avg_latency = statistics.mean(latencies) if latencies else 0.0

    print(f"\n=== Summary ({repeat} run(s) x {len(CASES)} cases = {total_cases} total) ===")
    print(f"Pass: {totals['pass']}/{total_cases} ({pass_rate * 100:.1f}%)")
    print(f"Avg selector latency: {avg_latency:.2f}s")
    print("\nPer-case pass rate:")
    for cid, results in per_case_pass_rates.items():
        rate = sum(results) / len(results) if results else 0.0
        print(f"  {cid}: {sum(results)}/{len(results)} ({rate * 100:.0f}%)")

    return 0 if totals["fail"] == 0 else 1


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live 9B tool-selector first-pick probe")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat all cases N times (temp=0, so mainly for latency stats)")
    args = parser.parse_args(argv)
    return run(args.repeat)


if __name__ == "__main__":
    sys.exit(main())
