"""Phase 3 live smoke test for issue #513 (parallel sub-agent fan-out).

Unlike Phase 1's isolation probe (which called specialist graphs directly, bypassing
routing.py entirely), this drives the REAL wired-in path: `create_parallel_delegate_adapter`
built from real `create_specialist_agents`, invoked with a state shaped exactly like what
`route_after_supervisor` would send it — i.e. a supervisor AIMessage carrying 2+
`delegate_to_*` tool_calls. This exercises the actual production code (event bus publishing,
VRAM swap, would_coexist partitioning, ToolMessage packaging), not a mock.

Two scenarios:
  1. ELIGIBLE  — traversal_agent + research_agent (both pin qwen/qwen3.5-9b) should partition
     into the CONCURRENT batch and actually overlap.
  2. INELIGIBLE — traversal_agent + code_agent (code_agent pins qwen3-coder-30b, no size-table
     entry -> unknown -> not eligible) should partition entirely into the SERIAL fallback,
     with code_agent never attempted concurrently.

Run (LM Studio must be up):
    .venv/Scripts/python scripts/parallel_delegate_live_smoke.py
    .venv/Scripts/python scripts/parallel_delegate_live_smoke.py --skip-ineligible
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage

from src.infrastructure.tools.registry import ToolRegistry
from src.shared.config_manager import ConfigManager
from src.core.orchestration.agents.factory import create_specialist_agents
from src.core.orchestration.handoffs import create_parallel_delegate_adapter
from src.core.orchestration.nodes.routing import make_route_after_supervisor

RECURSION_LIMIT = 15


def _delegate_call(agent_name: str, task: str, call_id: str) -> dict:
    return {"name": f"delegate_to_{agent_name}", "args": {"task": task}, "id": call_id}


async def _run_scenario(parallel_delegate_node, route, sub_agents_dict, title: str,
                         agent_tasks: dict, thread_id: str) -> None:
    print(f"\n=== SCENARIO: {title} ===")
    tool_calls = [
        _delegate_call(name, task, f"call_{i}")
        for i, (name, task) in enumerate(agent_tasks.items())
    ]
    state = {"messages": [AIMessage(content="", tool_calls=tool_calls)]}

    # Confirm route_after_supervisor actually sends this to parallel_delegate (not the
    # single-agent path) — the same decision the real graph would make.
    decision = route(state)
    print(f"route_after_supervisor decision: '{decision}' (expect 'parallel_delegate' for 2+ calls)")
    if decision != "parallel_delegate":
        print("  !! unexpected routing decision — aborting this scenario")
        return

    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
    t0 = time.perf_counter()
    result = await parallel_delegate_node(state, config)
    wall = time.perf_counter() - t0

    messages = result.get("messages", [])
    print(f"wall-clock: {wall:.1f}s   ToolMessages returned: {len(messages)} (expect {len(tool_calls)})")
    for m in messages:
        content = str(m.content)
        print(f"  [{m.name}] -> {content[:150]}{'...' if len(content) > 150 else ''}")

    if len(messages) != len(tool_calls):
        print("  !! MISMATCH: not every delegate call got a ToolMessage back — a call was dropped.")
    else:
        print("  OK: every delegate call resolved.")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ineligible", action="store_true",
                     help="only run the eligible (concurrent) scenario")
    args = ap.parse_args()

    print("Discovering tools...")
    registry = ToolRegistry()
    registry.discover_all(ConfigManager())
    checkpointer = MemorySaver()

    print("Building specialist agents (create_specialist_agents)...")
    sub_agents_dict = create_specialist_agents(model=None, tool_registry=registry, checkpointer=checkpointer)
    print(f"  agents available: {sorted(sub_agents_dict.keys())}")
    for required in ("traversal_agent", "research_agent"):
        if required not in sub_agents_dict:
            print(f"  !! '{required}' not available (check disabled_subagents in config.yaml) — aborting.")
            return

    parallel_delegate_node = create_parallel_delegate_adapter(sub_agents_dict)
    route = make_route_after_supervisor(sub_agents_dict)

    await _run_scenario(
        parallel_delegate_node, route, sub_agents_dict,
        title="ELIGIBLE — traversal_agent + research_agent (both qwen3.5-9b, should run CONCURRENT)",
        agent_tasks={
            "traversal_agent": "Check whether a file named 'CHAD_PROBE_MARKER_DOES_NOT_EXIST.tmp' "
                                "exists in the current working directory. Report yes or no.",
            "research_agent": "What is 9 times 6? Answer directly in one sentence; no need to search.",
        },
        thread_id="smoke_eligible",
    )

    if not args.skip_ineligible and "code_agent" in sub_agents_dict:
        await _run_scenario(
            parallel_delegate_node, route, sub_agents_dict,
            title="INELIGIBLE — traversal_agent + code_agent (code_agent unknown-size, should run SERIAL)",
            agent_tasks={
                "traversal_agent": "Check whether a file named 'CHAD_PROBE_MARKER_2.tmp' exists in "
                                    "the current working directory. Report yes or no.",
                "code_agent": "Do not write any files. Just reply with the word 'ok' and stop.",
            },
            thread_id="smoke_ineligible",
        )
        print("\n(check the log line above starting '[ PARALLEL_DELEGATE ]' — code_agent must be "
              "in the 'serial' list, never 'concurrent'.)")


if __name__ == "__main__":
    asyncio.run(main())
