"""litellm_probe.py — does the APP's LLM path (litellm.acompletion) serialize concurrent calls?

Raw httpx to LM Studio parallelizes (parallel_probe: 1.69-1.94x). LangSmith shows the 3
research workers START concurrently, yet they generate serially in LM Studio. So something
between research_graph.ainvoke and the HTTP request serializes. The workers all share ONE
adapter -> litellm.acompletion (a shared/global async client). This fires 3 concurrent
litellm.acompletion calls the SAME way the adapter does and compares to serial. If concurrent
~= serial here (but raw httpx parallelized), litellm's client is the bottleneck.

Run:  python -m evals.litellm_probe --workers 3
"""
from __future__ import annotations

import argparse
import asyncio
import time
from typing import List, Tuple

import litellm

MODEL = "openai/qwen/qwen3.5-9b"
API_BASE = "http://localhost:1234/v1"
API_KEY = "lm-studio"

_HEAVY_SYSTEM = ("You are a meticulous research specialist. " + (
    "Reason step by step, consider multiple angles, and be thorough but concise. " * 60))

PROMPTS = [
    "Explain how a suspension bridge carries load from the deck to the towers.",
    "Explain the brewing method of traditional Belgian lambic beer.",
    "Explain how the human kidney filters blood and regulates water balance.",
    "Explain how a lithium-ion battery charges and discharges at the electrode level.",
    "Explain how GPS satellites and receivers compute a position on Earth.",
    "Explain the life cycle of a mid-sized star from nebula to white dwarf.",
]


async def _one(prompt: str, idx: int, t0: float) -> Tuple[int, float, float, int, str]:
    start = time.perf_counter()
    off = start - t0
    try:
        resp = await litellm.acompletion(
            model=MODEL, api_base=API_BASE, api_key=API_KEY,
            messages=[{"role": "system", "content": _HEAVY_SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=800, stream=False,
        )
        toks = int(getattr(resp, "usage", {}).get("completion_tokens", 0) or
                   resp.usage.completion_tokens if getattr(resp, "usage", None) else 0)
        return idx, off, time.perf_counter() - start, toks, ""
    except Exception as e:  # noqa: BLE001
        return idx, off, time.perf_counter() - start, 0, f"{type(e).__name__}: {e}"


def _report(title: str, rows: List[Tuple], wall: float) -> None:
    print(f"\n=== {title} ===")
    for idx, off, dur, toks, err in sorted(rows):
        print(f"  worker {idx}  start@{off:5.1f}s  dur {dur:5.1f}s  tok {toks:4}  {err[:50]}")
    print(f"  wall-clock: {wall:.1f}s")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    n = min(args.workers, len(PROMPTS))
    prompts = PROMPTS[:n]
    litellm.suppress_debug_info = True

    # warmup
    await _one("Say ready.", 0, time.perf_counter())

    t0 = time.perf_counter()
    srows = []
    for i, p in enumerate(prompts):
        srows.append(await _one(p, i, t0))
    serial = time.perf_counter() - t0
    _report(f"SERIAL x{n} (litellm.acompletion)", srows, serial)

    print(f"\n>>> {n} CONCURRENT litellm.acompletion — watch LM Studio <<<")
    await asyncio.sleep(1.0)
    t0 = time.perf_counter()
    crows = await asyncio.gather(*[_one(p, i, t0) for i, p in enumerate(prompts)])
    conc = time.perf_counter() - t0
    _report(f"CONCURRENT x{n} (litellm.acompletion)", crows, conc)

    speedup = serial / conc if conc else 0
    print(f"\n================= VERDICT =================")
    print(f"serial {serial:.1f}s  concurrent {conc:.1f}s  speedup {speedup:.2f}x  (ideal ~{n}x)")
    if speedup <= 1.3:
        print("=> litellm.acompletion SERIALIZES concurrent calls — THIS is why research workers "
              "don't parallelize (raw httpx did). Fix: give each worker its own client/adapter, "
              "or bypass the shared litellm client for the fan-out.")
    else:
        print("=> litellm.acompletion parallelizes fine. The serialization is elsewhere "
              "(checkpointer sync I/O blocking the loop, or graph-level).")


if __name__ == "__main__":
    asyncio.run(main())
