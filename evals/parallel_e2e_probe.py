"""parallel_e2e_probe.py — the simplest possible end-to-end test of the real fan-out:

    ONE supervisor turn (AGENT lane, no plan/scout/approval) -> parallel_research -> N workers.

Deliberately trivial per item (2 quick facts each) so the whole thing finishes fast — this
isolates the supervisor -> parallel_research -> concurrent-workers path from the slow plan lane
and the multi-round research that blew the 300s tools-node timeout. We just want to confirm, quickly:
  - the supervisor actually CALLS parallel_research (not raw web_search or a serial delegate),
  - it fans out to N workers,
  - the workers run concurrently (peak overlap == N),
  - and it returns a grounded answer without timing out.

Run (prod must be up on :8767):  python -m evals.parallel_e2e_probe
"""
from __future__ import annotations

import asyncio
import sys
import time

# Windows console default codepage (cp1252) can't encode the checkmark below and crashes the
# probe right after it prints the real verdict numbers — reconfigure stdout to UTF-8 so the run
# doesn't look "broken" on the one line that's purely cosmetic.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from evals.live import probe_live
from evals.langsmith_trace import _load_env, fetch_session_trace, render_trace_markdown

# 3 well-known items, 2 trivial facts each => each worker needs ~1 search and finishes fast.
PROMPT = (
    "Research these three companies IN PARALLEL and give me a short 3-line report — for EACH one, "
    "find (1) the year it was founded and (2) the city of its headquarters. "
    "The three companies: Google, Amazon, Microsoft. Research all three at the same time."
)


def _peak_concurrent_web_search(session_substring: str) -> int:
    """Max number of web_search runs in flight at once — the real concurrency signal."""
    import os
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
            if r.id in seen or r.name != "web_search":
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
    conv = f"eval_par_e2e_{int(time.time())}"
    print(f"parallel_e2e_probe: conv={conv}  (agent lane, single supervisor turn)")
    print(f"prompt: {PROMPT}\n")

    t0 = time.time()
    tr = await probe_live(PROMPT, category="agent", conv_id=conv)
    dur = time.time() - t0

    print(f"=== turn done in {dur:.1f}s  error={tr.error} ===")
    print(f"tools called (WS view): {tr.tool_names}")
    print(f"\nfinal response:\n{(tr.final_response or '(none)')[:1200]}\n")

    # LangSmith nested truth
    s = fetch_session_trace(conv)
    print(render_trace_markdown(s))
    pr = s.tool_counts.get("parallel_research", 0)
    peak = _peak_concurrent_web_search(conv)
    print("\n================= VERDICT =================")
    print(f"parallel_research calls: {pr}   peak concurrent web_search: {peak}")
    if pr >= 1 and peak >= 2:
        print("=> Supervisor fanned out via parallel_research and workers ran concurrently. ✅")
    elif pr >= 1:
        print("=> parallel_research fired but workers didn't visibly overlap "
              "(each may have done 0-1 searches, or ran too fast to overlap).")
    else:
        print("=> parallel_research did NOT fire — supervisor used another path "
              f"(saw tools: {list(s.tool_counts)[:8]}). Steer harder or check binding.")


if __name__ == "__main__":
    asyncio.run(main())
