"""Phase 1 probe for issue #513 (parallel sub-agent fan-out).

parallel_probe.py already proved LM Studio can serve N concurrent requests to the SAME
loaded model. The open question for #513 is one level up: does invoking TWO DIFFERENT
compiled specialist LangGraph subgraphs concurrently (via asyncio.gather, each on its own
namespaced thread_id) actually overlap in wall-clock, and does it work correctly end to end
(tools resolve, checkpointer doesn't collide, no shared-state bugs) — the exact mechanism
`parallel_research`/`_run_research_worker` already uses one level down (handoffs.py), now
applied to `create_specialist_agent` graphs instead of research workers.

This deliberately stays OUTSIDE the supervisor graph: no routing.py, no handoffs.py changes,
no ToolCoordinator. It builds two specialist agents that are coexist-eligible today
(traversal_agent + research_agent both pin qwen/qwen3.5-9b — see AGENT_CONFIGS in
src/core/orchestration/agents/factory.py) and drives them exactly like a caller in
handoffs.py would: build_specialist_graph -> .ainvoke() with a namespaced thread_id.

Run (LM Studio must be up):
    .venv/Scripts/python scripts/parallel_delegate_isolation_probe.py
    .venv/Scripts/python scripts/parallel_delegate_isolation_probe.py --skip-serial
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("CHAD_TESTING", "false")  # want the REAL model-override path, not mocks

from langgraph.checkpoint.memory import MemorySaver

from src.infrastructure.tools.registry import ToolRegistry
from src.shared.config_manager import ConfigManager
from src.core.orchestration.agents.factory import create_specialist_agent

# Two agents pinned to the SAME small model today (qwen/qwen3.5-9b) — the coexist-eligible
# pairing #513's `would_coexist` gate is meant to green-light for real concurrent fan-out.
AGENT_A = "traversal_agent"
AGENT_B = "research_agent"

TASK_A = (
    "Check whether a file named 'CHAD_PROBE_MARKER_DOES_NOT_EXIST.tmp' exists in the "
    "current working directory. Just check and report yes or no — do not create it."
)
TASK_B = "What is 7 times 8? Answer directly in one short sentence; no need to search anything."

RECURSION_LIMIT = 15


async def _run_one(graph: Any, task: str, thread_id: str, tag: str) -> Tuple[str, float, str]:
    """Invoke one specialist graph on its own namespaced thread. Returns (tag, duration, outcome)."""
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
    t0 = time.perf_counter()
    try:
        result = await graph.ainvoke({"messages": [("human", task)]}, config=cfg)
        dur = time.perf_counter() - t0
        msgs = result.get("messages", [])
        last = msgs[-1].content if msgs else "(no messages)"
        outcome = (str(last)[:120] + "...") if last and len(str(last)) > 120 else str(last)
        return tag, dur, outcome
    except Exception as e:  # noqa: BLE001
        return tag, time.perf_counter() - t0, f"ERROR: {type(e).__name__}: {e}"


def _build_graphs() -> Tuple[Any, Any]:
    print("Discovering tools (ToolRegistry.discover_all)...")
    registry = ToolRegistry()
    registry.discover_all(ConfigManager())
    checkpointer = MemorySaver()

    print(f"Building specialist graphs: {AGENT_A}, {AGENT_B} ...")
    # `model` (base) is unused for both — each has a model_override in AGENT_CONFIGS pinning
    # qwen/qwen3.5-9b, which create_specialist_agent resolves internally.
    graph_a = create_specialist_agent(AGENT_A, model=None, tool_registry=registry, checkpointer=checkpointer)
    graph_b = create_specialist_agent(AGENT_B, model=None, tool_registry=registry, checkpointer=checkpointer)
    return graph_a, graph_b


def _report(title: str, rows) -> None:
    print(f"\n=== {title} ===")
    for tag, dur, outcome in rows:
        print(f"  [{tag}] {dur:.1f}s  -> {outcome}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-serial", action="store_true", help="only run the concurrent pair")
    args = ap.parse_args()

    graph_a, graph_b = _build_graphs()

    serial_total = None
    if not args.skip_serial:
        print(f"\nSERIAL baseline: {AGENT_A} then {AGENT_B}, one after another...")
        t0 = time.perf_counter()
        rows = [
            await _run_one(graph_a, TASK_A, "probe_serial_a", AGENT_A),
            await _run_one(graph_b, TASK_B, "probe_serial_b", AGENT_B),
        ]
        serial_total = time.perf_counter() - t0
        _report(f"SERIAL ({AGENT_A} -> {AGENT_B})", rows)

    print(f"\n>>> FIRING {AGENT_A} + {AGENT_B} CONCURRENTLY NOW — watch LM Studio <<<")
    await asyncio.sleep(1.0)
    t0 = time.perf_counter()
    crows = await asyncio.gather(
        _run_one(graph_a, TASK_A, "probe_concurrent_a", AGENT_A),
        _run_one(graph_b, TASK_B, "probe_concurrent_b", AGENT_B),
    )
    conc_total = time.perf_counter() - t0
    _report(f"CONCURRENT ({AGENT_A} + {AGENT_B})", crows)

    print("\n================= VERDICT =================")
    slowest = max(dur for _, dur, _ in crows)
    total_individual = sum(dur for _, dur, _ in crows)
    print(f"concurrent wall-clock: {conc_total:.1f}s   slowest single agent: {slowest:.1f}s   "
          f"sum of both: {total_individual:.1f}s")
    if serial_total:
        speedup = serial_total / conc_total if conc_total else 0
        print(f"serial wall-clock: {serial_total:.1f}s   speedup: {speedup:.2f}x  (ideal ~2x)")
        if speedup >= 1.5:
            print("=> Two DIFFERENT specialist subgraphs run concurrently for real. "
                  "Phase 1 mechanism proven — safe to move to Phase 2 (routing/graph wiring).")
        elif speedup <= 1.15:
            print("=> No meaningful speedup — concurrent agent invocation is being serialized "
                  "somewhere (LM Studio max_concurrent, lmstudio_gate, or the checkpointer). "
                  "Do NOT proceed to Phase 2 until this is root-caused — check the SAME "
                  "discrepancy noted in config.yaml re: parallel_research not always showing "
                  "true concurrency in production.")
        else:
            print("=> PARTIAL overlap. Investigate before proceeding — compare against "
                  "evals/parallel_probe.py's raw-HTTP concurrency numbers for the same model "
                  "to see whether the gap is in LangGraph/checkpointer overhead or the gate.")
    else:
        print(f"(no serial baseline run — pass without --skip-serial to get a speedup number)")
        print("If wall-clock ~= slowest single agent => real concurrency; "
              "if wall-clock ~= sum of both => serialized.")


if __name__ == "__main__":
    asyncio.run(main())
