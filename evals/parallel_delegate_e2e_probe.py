"""parallel_delegate_e2e_probe.py — end-to-end check of #513's real fan-out path:

    ONE supervisor turn (AGENT lane) -> 2 delegate_to_* calls in the same turn -> parallel_delegate
    node -> concurrent-eligible specialists overlap.

Mirrors parallel_e2e_probe.py's shape (the #510 `parallel_research` e2e check) but one level up
the stack — this checks specialist DELEGATION fan-out, not research-worker fan-out. Deliberately
uses two agents pinned to the SAME small model today (traversal_agent + research_agent, both
qwen/qwen3.5-9b — see AGENT_CONFIGS in src/core/orchestration/agents/factory.py) so the
VRAM-eligibility gate (model_lifecycle.would_coexist) should green-light concurrent execution
rather than fall back to serial. Confirms:
  - the supervisor emits 2 delegate_to_* calls in ONE turn for a genuinely independent
    multi-domain ask (tests the persona rule added in supervisor_node.py),
  - route_after_supervisor sends that turn to parallel_delegate (not a single agent node),
  - both delegates actually resolve, and their execution windows overlap (peak concurrency >= 2).

Run (server must be up — see evals/live.py docstring for how to start one):
    python -m evals.parallel_delegate_e2e_probe
    python -m evals.parallel_delegate_e2e_probe --server ws://localhost:8788/ws   # dev Brain
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from evals.live import probe_live
from evals.langsmith_trace import _load_env, fetch_session_trace, render_trace_markdown

PROMPT = (
    "Do two completely independent things at once, in parallel: "
    "(1) check whether a file named 'CHAD_PROBE_MARKER_DOES_NOT_EXIST.tmp' exists in Google "
    "Drive or the local filesystem, and report yes or no; "
    "(2) research what year Google was founded and report just the year. "
    "These two tasks are unrelated to each other — handle them at the same time, not one after "
    "the other."
)

# The two delegate targets this prompt should trigger — both pinned to qwen/qwen3.5-9b today,
# so they're expected to be VRAM-eligible for the concurrent batch (see would_coexist).
EXPECTED_DELEGATES = ("delegate_to_traversal_agent", "delegate_to_research_agent")


def _peak_concurrent(session_substring: str, run_names: tuple) -> int:
    """Max number of `run_names` runs in flight at once — the real concurrency signal.
    Same technique as parallel_e2e_probe.py's _peak_concurrent_web_search, generalized to
    arbitrary run names (here: the two delegate tool calls, not web_search)."""
    from langsmith import Client
    _load_env()
    if not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        return -1
    c = Client()
    proj = os.environ.get("LANGSMITH_PROJECT", "ChadAi")
    roots = [r for r in c.list_runs(project_name=proj, filter='eq(name, "ChadTurn")', limit=30)
             if session_substring in str((r.extra or {}).get("metadata", {}).get("session_id", ""))]
    evs = []
    seen = set()
    for root in roots:
        for r in c.list_runs(project_name=proj, trace_id=root.trace_id):
            if r.id in seen or r.name not in run_names:
                continue
            seen.add(r.id)
            if r.start_time and r.end_time:
                evs.append((r.start_time, 1))
                evs.append((r.end_time, -1))
    evs.sort()
    cur = mx = 0
    for _, d in evs:
        cur += d
        mx = max(mx, cur)
    return mx


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=None, help="override CHAD_SERVER_URL (e.g. ws://localhost:8788/ws for dev)")
    args = ap.parse_args()
    if args.server:
        os.environ["CHAD_SERVER_URL"] = args.server

    conv = f"eval_par_delegate_e2e_{int(time.time())}"
    print(f"parallel_delegate_e2e_probe: conv={conv}  (agent lane, single supervisor turn)")
    print(f"prompt: {PROMPT}\n")

    t0 = time.time()
    tr = await probe_live(PROMPT, category="agent", conv_id=conv)
    dur = time.time() - t0

    print(f"=== turn done in {dur:.1f}s  error={tr.error} ===")
    print(f"tools called (WS view): {tr.tool_names}")
    print(f"\nfinal response:\n{(tr.final_response or '(none)')[:1200]}\n")

    s = fetch_session_trace(conv)
    print(render_trace_markdown(s))

    delegate_hits = {name: s.tool_counts.get(name, 0) for name in EXPECTED_DELEGATES}
    both_fired = all(v >= 1 for v in delegate_hits.values())
    peak = _peak_concurrent(conv, EXPECTED_DELEGATES)

    print("\n================= VERDICT =================")
    print(f"delegate calls: {delegate_hits}   peak concurrent: {peak}")
    if both_fired and peak >= 2:
        print("=> Supervisor delegated to BOTH specialists in one turn and they ran "
              "CONCURRENTLY via parallel_delegate. ✅ (#513 end-to-end confirmed)")
    elif both_fired:
        print("=> Both delegate_to_* calls fired but didn't visibly overlap — either they "
              "ran too fast to overlap, or would_coexist gated them to serial (check "
              "'[ PARALLEL_DELEGATE ]' server logs for the concurrent/serial split) — or "
              "LANGSMITH_API_KEY isn't set (peak=-1 means no trace data, not serialization).")
    else:
        print(f"=> Supervisor did NOT emit both delegate_to_* calls in one turn "
              f"(saw tools: {list(s.tool_counts)[:8]}). Either it delegated only one at a "
              "time (persona rule not followed this run — local models are non-deterministic, "
              "retry), or it solved the task another way. Not necessarily a bug on one run — "
              "re-run a few times before concluding the persona rule needs steering harder.")


if __name__ == "__main__":
    asyncio.run(main())
