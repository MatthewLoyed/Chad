"""voice_latency_bench.py — benchmark the no-LLM fast-lane router against the current
9B voice lane, for issue #654 problem 3 ("voice lane too slow for quick commands").

Two things get measured against mock transcripts (no microphone, no wake word, no
manual voice testing needed):

  1. FAST LANE prototype (`voice_fast_lane.match_fast_lane`) — regex tier + embedding
     fallback, no model call at all.
  2. BASELINE — the current production path: `voice_node.voice_responder` via the real
     in-process turn pipeline (`evals/inprocess.py`), category="voice", so it hits the
     actual `qwen3.5-9b` adapter exactly like a live wake-word turn would. Requires
     LM Studio (or configured provider) running with the voice model — pass
     --baseline to include it; omitted by default since it needs a live model server
     and is slow enough to matter (this is the whole point of the comparison).

Budget targets, from issue #654's "Latency framing for later" framing:
  - fast lane:  ~440ms   (quick commands: open app/site, web search, media, smart home)
  - slow lane:  700-900ms (dictation/agent handoff acknowledgment)
  - "broken":   >1000ms feels broken regardless of lane

Run:
  python -m evals.voice_latency_bench                # fast-lane only, no model needed
  python -m evals.voice_latency_bench --baseline      # + real 9B baseline (needs LM Studio up)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DATASET_PATH = Path(__file__).parent / "datasets" / "voice_fast_lane.jsonl"

FAST_LANE_TARGET_MS = 440
SLOW_LANE_TARGET_MS = (700, 900)
BROKEN_CEILING_MS = 1000


def load_cases() -> List[Dict[str, Any]]:
    cases = []
    with DATASET_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def run_fast_lane(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from src.core.orchestration.nodes.voice_fast_lane import match_fast_lane, _get_default_embedding_router

    # Warm the embedding model + prototype vectors OUTSIDE the timed loop. In production
    # this model is already resident (shared with tool-RAG), so a cold ~19s SentenceTransformer
    # load has no business being counted against per-command router latency.
    router = _get_default_embedding_router()
    t_warm = time.perf_counter()
    router._ensure_prototypes()
    warm_s = time.perf_counter() - t_warm
    if warm_s > 0.5:
        print(f"(embedding model cold-load: {warm_s:.1f}s — one-time cost, excluded from "
              f"per-command timings below; production keeps this warm)")

    results = []
    for case in cases:
        t0 = time.perf_counter()
        hit = match_fast_lane(case["query"], embedding_router=router)
        wall_ms = (time.perf_counter() - t0) * 1000

        predicted_lane = "fast" if hit is not None else "escalate"
        expect_lane = case["expect_lane"]
        lane_correct = predicted_lane == expect_lane
        action_correct = (
            hit is not None and hit.action == case.get("expect_action")
        ) if expect_lane == "fast" else (hit is None)

        results.append({
            "id": case["id"],
            "query": case["query"],
            "expect_lane": expect_lane,
            "expect_action": case.get("expect_action"),
            "predicted_lane": predicted_lane,
            "predicted_action": hit.action if hit else None,
            "method": hit.method if hit else None,
            "confidence": round(hit.confidence, 3) if hit else None,
            "wall_ms": round(wall_ms, 2),
            "lane_correct": lane_correct,
            "action_correct": action_correct,
        })
    return results


async def run_baseline(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from evals.inprocess import build_inprocess_session, run_turn_with

    fast_cases = [c for c in cases if c["expect_lane"] == "fast"]
    print(f"Building in-process session (real container, category='voice')... "
          f"this loads the actual voice model.")
    container, warmup_err = await build_inprocess_session()
    if warmup_err:
        print(f"  (warmup non-fatal error: {warmup_err})")

    results = []
    for case in fast_cases:
        trace = await run_turn_with(container, case["query"], category="voice")
        wall_ms = trace["duration_s"] * 1000
        results.append({
            "id": case["id"],
            "query": case["query"],
            "wall_ms": round(wall_ms, 2),
            "response": trace.get("response"),
            "error": trace.get("error"),
        })
    return results


def print_fast_lane_table(results: List[Dict[str, Any]]) -> None:
    print("\n=== FAST LANE (no-LLM prototype) ===")
    print(f"{'id':<28} {'lane':<9} {'action':<12} {'method':<10} {'ms':>7}  {'ok'}")
    for r in results:
        ok = "OK" if r["lane_correct"] and r["action_correct"] else "MISS"
        print(f"{r['id']:<28} {r['predicted_lane']:<9} "
              f"{str(r['predicted_action']):<12} {str(r['method']):<10} "
              f"{r['wall_ms']:>7.2f}  {ok}")

    lane_acc = sum(r["lane_correct"] for r in results) / len(results)
    action_acc = sum(r["action_correct"] for r in results) / len(results)
    fast_only = [r for r in results if r["expect_lane"] == "fast"]
    fast_times = [r["wall_ms"] for r in fast_only]

    print(f"\nlane accuracy:   {lane_acc*100:.0f}% ({sum(r['lane_correct'] for r in results)}/{len(results)})")
    print(f"action accuracy: {action_acc*100:.0f}% ({sum(r['action_correct'] for r in results)}/{len(results)})")
    if fast_times:
        print(f"fast-lane latency: mean={statistics.mean(fast_times):.2f}ms "
              f"p95={sorted(fast_times)[max(0, int(len(fast_times)*0.95)-1)]:.2f}ms "
              f"max={max(fast_times):.2f}ms")
        over_budget = [r for r in fast_only if r["wall_ms"] > FAST_LANE_TARGET_MS]
        if over_budget:
            print(f"  [!] {len(over_budget)} case(s) exceeded {FAST_LANE_TARGET_MS}ms target: "
                  f"{[r['id'] for r in over_budget]}")
        else:
            print(f"  [OK] all fast-lane cases under {FAST_LANE_TARGET_MS}ms target "
                  f"(router latency only — does not include VAD/STT endpointing time)")


def print_baseline_table(results: List[Dict[str, Any]]) -> None:
    print("\n=== BASELINE (current production: 9B voice_node via real turn pipeline) ===")
    print(f"{'id':<28} {'ms':>9}  {'response'}")
    for r in results:
        resp = (r["response"] or r.get("error") or "")[:60]
        print(f"{r['id']:<28} {r['wall_ms']:>9.0f}  {resp}")

    times = [r["wall_ms"] for r in results if r["error"] is None]
    if times:
        print(f"\nbaseline latency: mean={statistics.mean(times):.0f}ms "
              f"min={min(times):.0f}ms max={max(times):.0f}ms")
        print(f"target: fast lane ~{FAST_LANE_TARGET_MS}ms, "
              f"slow lane {SLOW_LANE_TARGET_MS[0]}-{SLOW_LANE_TARGET_MS[1]}ms, "
              f"broken >{BROKEN_CEILING_MS}ms")
        over_broken = [r for r in results if r["wall_ms"] > BROKEN_CEILING_MS]
        print(f"  {len(over_broken)}/{len(results)} cases exceed the {BROKEN_CEILING_MS}ms "
              f"'feels broken' ceiling")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true",
                         help="Also run the real 9B voice lane via in-process turn pipeline "
                              "(needs LM Studio/provider up with the voice model).")
    args = parser.parse_args()

    cases = load_cases()
    print(f"Loaded {len(cases)} mock voice transcripts from {DATASET_PATH.name}")

    fast_results = run_fast_lane(cases)
    print_fast_lane_table(fast_results)

    if args.baseline:
        baseline_results = asyncio.run(run_baseline(cases))
        print_baseline_table(baseline_results)

        fast_only = [r for r in fast_results if r["expect_lane"] == "fast"]
        fast_mean = statistics.mean(r["wall_ms"] for r in fast_only)
        base_times = [r["wall_ms"] for r in baseline_results if r["error"] is None]
        if base_times:
            base_mean = statistics.mean(base_times)
            print(f"\n=== VERDICT ===")
            print(f"fast-lane router: {fast_mean:.2f}ms  vs  baseline 9B: {base_mean:.0f}ms  "
                  f"=> {base_mean/max(fast_mean, 0.01):.0f}x faster")
    else:
        print("\n(pass --baseline to compare against the real 9B voice lane; "
              "requires LM Studio running with the voice model)")


if __name__ == "__main__":
    main()
