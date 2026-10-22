"""One-off live probe: plan-mode Scout tuning check (Aruba honeymoon prompt).

Sends a single planning-category turn to the dev server on :8788 (started from this
worktree, separate from prod :8767) and reports what Scout/planner actually did:
tool calls made, which web_search backend fired, the gather brief, and the plan.
Does NOT send an approval — the turn is expected to stop at awaiting_approval.
"""
import asyncio
import json
import os

os.environ.setdefault("CHAD_SERVER_URL", "ws://localhost:8788/ws")

from evals.live import probe_live  # noqa: E402

PROMPT = (
    "plan me a honeymoon to aruba, find real facts, cost, we want all inclusive resort "
    "and activites like snorkeling and lowkey hiking. find everything and give us options "
    "and recommendations. find her lingerie too."
)


async def main():
    trace = await probe_live(PROMPT, category="planning")

    print("=== STATUS ===")
    print("status:", trace.status)
    print("stop_reason:", trace.stop_reason)
    print("error:", trace.error)
    print("duration_s:", trace.duration_s)

    print("\n=== TOOL CALLS (in order) ===")
    for tc in trace.tool_calls:
        print(json.dumps(tc, default=str)[:400])

    print("\n=== TOOL NAMES ===")
    print(trace.tool_names)

    print("\n=== GATHER BRIEF (Scout output) ===")
    print(trace.gather_brief)

    print("\n=== PLAN ===")
    print(json.dumps(trace.plan, indent=2, default=str) if trace.plan else "(none)")

    print("\n=== TOOL RESULTS (search backend evidence) ===")
    print(trace.tool_results[:8000])

    print("\n=== FINAL RESPONSE / TIMELINE ===")
    print(trace.timeline)
    print(trace.final_response)


if __name__ == "__main__":
    asyncio.run(main())
