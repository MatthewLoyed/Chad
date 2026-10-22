"""benchmark_premodel.py — precise per-step breakdown of the PRE-MODEL phase.

Answers "why is the first token ~20s away?" by attributing the gap to named
pipeline steps (history fetch, intent classification, tool selection, prompt
assembly, preflight, checkpoint check, main-model preload) rather than the coarse
black-box `pre_model_ms` the summary benchmark reports.

How it works: the Brain records an exact per-step timing on every turn
(`TurnPerfTracker`, `src/core/orchestration/turn/perf.py`) and ships it out on the
final `token_usage` WebSocket event as `pre_model_breakdown`. This script sends real
turns to the live server, reads that field, and prints a ranked table + mean/min/max
across iterations.

    # Prod Brain must be running on :8767 (never start/kill it yourself).
    $env:PYTHONPATH="."; .venv\\Scripts\\python.exe scripts\\benchmark_premodel.py
    $env:PYTHONPATH="."; .venv\\Scripts\\python.exe scripts\\benchmark_premodel.py --prompt "search my email for invoices" -n 5

NOTE: the `pre_model_breakdown` field only exists on server builds that include the
TurnPerfTracker instrumentation. If every turn reports "no breakdown", the running
Brain predates it — ship an update to prod, then re-run.
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_pipeline import (  # noqa: E402
    load_auth_token, check_server_health, _make_msg, SERVER_URL, A, section, ok, warn, err, info,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Canonical order the steps run in the coordinator (for stable table rows).
STEP_ORDER = [
    "history", "intent", "mcp_wait", "tools", "prompt",
    "tokens", "preflight", "checkpoint", "preload",
]


async def _one_turn(ws, prompt: str, conv_id: str, category: str) -> dict:
    t0 = time.time()
    await ws.send(_make_msg("user_input", {
        "text": prompt, "initiator": "user", "conversation_id": conv_id, "category": category,
    }))
    deadline = t0 + 300
    breakdown = None
    ttft_ms = None
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=120)
        except asyncio.TimeoutError:
            break
        try:
            evt = json.loads(raw)
        except Exception:
            continue
        etype = evt.get("type", "")
        p = evt.get("payload", {}) or {}
        if etype == "ai_response" and p.get("fragment") and ttft_ms is None:
            ttft_ms = (time.time() - t0) * 1000
        if etype == "token_usage" and p.get("pre_model_breakdown"):
            breakdown = p["pre_model_breakdown"]
        if etype == "ai_response" and not p.get("fragment"):
            break
        if etype in ("error", "system_error"):
            err(f"server error: {p}")
            break
    return {"breakdown": breakdown, "ttft_ms": ttft_ms}


async def run(prompt: str, iterations: int, category: str):
    import websockets
    token = load_auth_token()
    if not check_server_health():
        err("Prod Brain not healthy on :8767. Start it (or ask the user) — do not kill it.")
        return
    headers = {"Origin": "http://localhost:5173"}
    try:
        ctx = websockets.connect(SERVER_URL, additional_headers=headers, ping_interval=None, open_timeout=20)
    except TypeError:
        ctx = websockets.connect(SERVER_URL, extra_headers=headers, ping_interval=None, open_timeout=20)

    results = []
    async with ctx as ws:
        await ws.send(_make_msg("auth", auth_token=token))
        await asyncio.wait_for(ws.recv(), timeout=30)
        ok("Authenticated")
        for i in range(iterations):
            section(f"Iteration {i + 1} / {iterations}")
            info(f"→ {prompt!r} (category={category})")
            r = await _one_turn(ws, prompt, str(uuid.uuid4()), category)
            if r["breakdown"]:
                steps = r["breakdown"].get("steps", {})
                total = r["breakdown"].get("total_ms", 0)
                line = "  ".join(f"{k}={steps.get(k, 0):.0f}" for k in STEP_ORDER if k in steps)
                ok(f"pre_model total={total:.0f}ms  ttft={r['ttft_ms'] or 0:.0f}ms  ::  {line}")
            else:
                warn("no pre_model_breakdown on this turn (server may predate the instrumentation)")
            results.append(r)
    _report(results)


def _report(results: list):
    section("Pre-Model Breakdown (mean across iterations)")
    got = [r["breakdown"] for r in results if r.get("breakdown")]
    if not got:
        err("No breakdowns captured. The running Brain lacks TurnPerfTracker — ship an update to prod and re-run.")
        return

    # Collect per-step samples.
    samples: dict = {k: [] for k in STEP_ORDER}
    totals, extras = [], {"tool_9b_ms": [], "shortlist_ms": [], "shortlist_size": []}
    tool_paths = []
    for b in got:
        steps = b.get("steps", {})
        for k in STEP_ORDER:
            if k in steps:
                samples[k].append(steps[k])
        totals.append(b.get("total_ms", 0))
        for ek in ("tool_9b_ms", "shortlist_ms", "shortlist_size"):
            if b.get(ek) is not None:
                extras[ek].append(b[ek])
        if b.get("tool_path"):
            tool_paths.append(b["tool_path"])

    def stat(vals):
        if not vals:
            return (0.0, 0.0, 0.0)
        return (sum(vals) / len(vals), min(vals), max(vals))

    total_mean = (sum(totals) / len(totals)) if totals else 1.0 or 1.0
    rows = [(k, stat(samples[k])) for k in STEP_ORDER if samples[k]]
    rows.sort(key=lambda r: r[1][0], reverse=True)

    print(f"\n  Turns with breakdown : {len(got)} / {len(results)}")
    print(f"  Pre-model total (mean): {total_mean:.0f}ms\n")
    header = f"  {'Step':<12} {'Mean':>9} {'Min':>9} {'Max':>9} {'%':>6}   Chart"
    print(header)
    print("  " + "─" * (len(header) + 8))
    max_mean = max((r[1][0] for r in rows), default=1) or 1
    for name, (mean, lo, hi) in rows:
        pct = (mean / total_mean * 100) if total_mean else 0
        bar = A["info"] + "█" * int((mean / max_mean) * 28) + A["reset"]
        print(f"  {name:<12} {mean:>8.0f}ms {lo:>8.0f}ms {hi:>8.0f}ms {pct:>5.0f}%   {bar}")

    # Split the "tools" bucket into RAG shortlist (time-to-reach-9B) vs 9B inference.
    if extras["tool_9b_ms"] or extras["shortlist_ms"]:
        m9 = stat(extras["tool_9b_ms"])[0] if extras["tool_9b_ms"] else 0.0
        msl = stat(extras["shortlist_ms"])[0] if extras["shortlist_ms"] else 0.0
        print(f"\n  tools bucket split:  RAG shortlist (embed+RPC, = time to reach 9B)={msl:.0f}ms"
              f"  |  9B inference={m9:.0f}ms")
    if extras["shortlist_size"]:
        s, _, _ = stat(extras["shortlist_size"])
        print(f"  hybrid shortlist size (mean): {s:.0f} tools")
    if tool_paths:
        print(f"  tool selection path(s): {sorted(set(tool_paths))}")

    if rows:
        top, (tmean, _, _) = rows[0]
        print(f"\n  {A['bold']}Largest step:{A['reset']} {top} "
              f"({tmean:.0f}ms, {tmean / total_mean * 100:.0f}% of pre-model)")


def main():
    ap = argparse.ArgumentParser(description="Precise per-step pre-model latency breakdown.")
    ap.add_argument("--prompt", default="what time is it")
    ap.add_argument("--iterations", "-n", type=int, default=3)
    ap.add_argument("--category", default="auto",
                    help="auto (uses intent router) | agent (skips it) | chat")
    a = ap.parse_args()
    section("Chad Pre-Model Benchmark")
    print(f"  Prompt     : {a.prompt}")
    print(f"  Iterations : {a.iterations}")
    print(f"  Category   : {a.category}")
    asyncio.run(run(a.prompt, a.iterations, a.category))


if __name__ == "__main__":
    main()
