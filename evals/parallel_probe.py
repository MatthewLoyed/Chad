"""parallel_probe.py — isolate the ONE question parallel_research depends on:

    does LM Studio actually run N concurrent 9B requests in PARALLEL,
    or does it queue them and run them one at a time?

parallel_research fans out to N workers that ALL hit the SAME loaded 9B model instance
(same model_id). If LM Studio serves one loaded model's concurrent requests serially
(default llama.cpp `--parallel 1`, no continuous batching), then the "fan-out" is a lie:
the wall-clock is ~N x a single worker, and you see exactly the gaps between 9B calls that
you're watching in the UI. This probe measures that directly — no scout/planner/selector
overhead in the way — so we know whether the concurrency is real BEFORE trusting the
plan-lane numbers.

What it does:
  1. Warm up (load the model).
  2. SERIAL baseline: send N distinct prompts one after another; record each latency.
  3. CONCURRENT: send the SAME N prompts all at once (asyncio.gather); record each latency
     and the batch wall-clock.
  4. Verdict: speedup = serial_total / concurrent_total.
        ~N  -> true parallel inference (great).
        ~1  -> LM Studio is SERIALIZING the requests (fan-out gives no speedup).

Run (watch LM Studio while it prints "FIRING N CONCURRENT"):
    python -m evals.parallel_probe --workers 3
    python -m evals.parallel_probe --workers 8 --model qwen/qwen3.5-9b
"""
from __future__ import annotations

import argparse
import asyncio
import time
from typing import List, Optional, Tuple

import httpx

BASE_URL = "http://localhost:1234/v1"

# Distinct prompts — each forces a substantial, self-contained generation so a single
# request takes long enough (~10-25s) to visibly observe overlap in LM Studio.
PROMPTS: List[str] = [
    "Explain in about 300 words how a suspension bridge carries load from the deck to the towers.",
    "Write about 300 words on the history and brewing method of traditional Belgian lambic beer.",
    "Describe in about 300 words how the human kidney filters blood and regulates water balance.",
    "Explain in about 300 words how a lithium-ion battery charges and discharges at the electrode level.",
    "Write about 300 words on how GPS satellites and receivers compute a position on Earth.",
    "Describe in about 300 words the life cycle of a mid-sized star from nebula to white dwarf.",
    "Explain in about 300 words how espresso extraction works and what variables change the taste.",
    "Write about 300 words on how the TCP three-way handshake establishes a reliable connection.",
]


# A big filler system prompt to mimic a research worker's real request shape (system + tools +
# context ~ a couple thousand tokens) so we can test whether LM Studio still batches when each
# request has a LARGE KV-cache footprint (vs the tiny prompts that batched fine).
_HEAVY_SYSTEM = ("You are a meticulous research specialist. " + (
    "Follow your instructions carefully and reason step by step before answering. "
    "Consider multiple angles, cite sources, and be thorough but concise. " * 60
))

# module-level knobs set from args (keeps _one_call signature stable)
_HEAVY = False
_MAX_TOKENS = 500


async def _one_call(client: httpx.AsyncClient, model: str, prompt: str, idx: int,
                    batch_t0: float) -> Tuple[int, float, float, int, str]:
    """Return (idx, start_offset_s, duration_s, completion_tokens, error)."""
    start = time.perf_counter()
    start_off = start - batch_t0
    messages = ([{"role": "system", "content": _HEAVY_SYSTEM}] if _HEAVY else []) + \
               [{"role": "user", "content": prompt}]
    try:
        r = await client.post(
            f"{BASE_URL}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": _MAX_TOKENS,
                "stream": False,
            },
            timeout=600.0,
        )
        r.raise_for_status()
        data = r.json()
        toks = int((data.get("usage") or {}).get("completion_tokens") or 0)
        dur = time.perf_counter() - start
        return idx, start_off, dur, toks, ""
    except Exception as e:  # noqa: BLE001
        return idx, start_off, time.perf_counter() - start, 0, f"{type(e).__name__}: {e}"


async def _serial(client, model, prompts) -> List[Tuple]:
    rows = []
    t0 = time.perf_counter()
    for i, p in enumerate(prompts):
        rows.append(await _one_call(client, model, p, i, t0))
    return rows


async def _concurrent(client, model, prompts) -> List[Tuple]:
    t0 = time.perf_counter()
    tasks = [_one_call(client, model, p, i, t0) for i, p in enumerate(prompts)]
    return await asyncio.gather(*tasks)


def _report(title: str, rows: List[Tuple], wall: float) -> None:
    print(f"\n=== {title} ===")
    print(f"{'worker':>6} {'start@':>8} {'dur':>8} {'tok':>6}  {'tok/s':>7}  err")
    for idx, off, dur, toks, err in sorted(rows):
        tps = (toks / dur) if dur > 0 else 0
        print(f"{idx:>6} {off:>7.1f}s {dur:>7.1f}s {toks:>6} {tps:>7.1f}  {err[:40]}")
    total_tok = sum(r[3] for r in rows)
    print(f"  wall-clock: {wall:.1f}s   total tokens: {total_tok}   "
          f"aggregate tok/s: {(total_tok/wall if wall else 0):.1f}")


def _prompt_for(i: int) -> str:
    """Distinct prompt for worker i, cycling + tagging so >len(PROMPTS) stay unique."""
    base = PROMPTS[i % len(PROMPTS)]
    if i < len(PROMPTS):
        return base
    return f"(variant {i}) {base}"


async def _sweep(client, model, counts: List[int], max_tokens: int) -> None:
    """Concurrent-only batches at escalating worker counts to find where concurrency breaks:
    aggregate tok/s stops rising, per-worker latency balloons, or requests start erroring."""
    # single-request baseline for the speedup column
    base = await _one_call_tok(client, model, _prompt_for(0), 0, time.perf_counter(), max_tokens)
    single_dur = base[2] or 1e-9
    single_tps = (base[3] / single_dur) if single_dur else 0
    print(f"\nbaseline 1 req: {single_dur:.1f}s, {single_tps:.1f} tok/s")
    print(f"\n{'N':>4} {'wall':>7} {'slowest':>8} {'agg tok/s':>10} {'speedup':>8} {'errors':>7}")
    for n in counts:
        prompts = [_prompt_for(i) for i in range(n)]
        t0 = time.perf_counter()
        rows = await asyncio.gather(*[
            _one_call_tok(client, model, p, i, t0, max_tokens) for i, p in enumerate(prompts)
        ])
        wall = time.perf_counter() - t0
        slowest = max(r[2] for r in rows)
        toks = sum(r[3] for r in rows)
        errs = sum(1 for r in rows if r[4])
        agg = toks / wall if wall else 0
        speedup = agg / single_tps if single_tps else 0
        print(f"{n:>4} {wall:>6.1f}s {slowest:>7.1f}s {agg:>10.1f} {speedup:>7.2f}x {errs:>7}")
    print("\nRead the knee: aggregate tok/s climbs while it's truly batching, then FLATTENS "
          "(GPU saturated) or drops / errors appear (over capacity). That N is the ceiling.")


async def _one_call_tok(client, model, prompt, idx, batch_t0, max_tokens):
    """Same as _one_call but with a caller-set max_tokens (sweep uses a smaller budget)."""
    start = time.perf_counter()
    start_off = start - batch_t0
    try:
        r = await client.post(
            f"{BASE_URL}/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.7, "max_tokens": max_tokens, "stream": False},
            timeout=600.0,
        )
        r.raise_for_status()
        toks = int((r.json().get("usage") or {}).get("completion_tokens") or 0)
        return idx, start_off, time.perf_counter() - start, toks, ""
    except Exception as e:  # noqa: BLE001
        return idx, start_off, time.perf_counter() - start, 0, f"{type(e).__name__}: {e}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--model", default="qwen/qwen3.5-9b")
    ap.add_argument("--skip-serial", action="store_true",
                    help="only run the concurrent batch (faster; less to compare against)")
    ap.add_argument("--sweep", default="",
                    help="comma list of worker counts to sweep, e.g. 1,2,3,4,6,8,12,16 "
                         "(concurrent-only; finds the concurrency ceiling)")
    ap.add_argument("--sweep-tokens", type=int, default=300,
                    help="max_tokens per request during --sweep (smaller = faster sweep)")
    ap.add_argument("--heavy", action="store_true",
                    help="mimic a research worker's request shape: big system prompt (large KV "
                         "footprint) + more max_tokens. Tests whether LM Studio still batches "
                         "concurrent LARGE-context requests (vs tiny ones).")
    args = ap.parse_args()

    global _HEAVY, _MAX_TOKENS
    _HEAVY = args.heavy
    if args.heavy:
        _MAX_TOKENS = 800
        print("[heavy mode] big system prompt + max_tokens=800 (worker-shaped requests)")

    if args.sweep:
        counts = [int(x) for x in args.sweep.split(",") if x.strip()]
        print(f"parallel_probe SWEEP: model={args.model}  counts={counts}  "
              f"max_tokens={args.sweep_tokens}  base={BASE_URL}")
        async with httpx.AsyncClient() as client:
            print("\nwarming up (loading model)…")
            w = await _one_call_tok(client, args.model, "Say ready.", 0, time.perf_counter(), 8)
            if w[4]:
                print(f"  !! warmup failed: {w[4]}"); return
            print(f"  warm ({w[2]:.1f}s).")
            await _sweep(client, args.model, counts, args.sweep_tokens)
        return

    n = max(1, min(args.workers, len(PROMPTS)))
    prompts = PROMPTS[:n]
    print(f"parallel_probe: model={args.model}  workers={n}  base={BASE_URL}")

    async with httpx.AsyncClient() as client:
        # 1. Warmup (JIT-load the model so load time doesn't pollute the timings)
        print("\nwarming up (loading model)…")
        w = await _one_call(client, args.model, "Say 'ready' in one word.", 0, time.perf_counter())
        if w[4]:
            print(f"  !! warmup failed: {w[4]}\n  is LM Studio up and is '{args.model}' available?")
            return
        print(f"  warm ({w[2]:.1f}s).")

        serial_total = None
        if not args.skip_serial:
            print(f"\nSERIAL baseline: {n} prompts one after another…")
            t0 = time.perf_counter()
            srows = await _serial(client, args.model, prompts)
            serial_total = time.perf_counter() - t0
            _report(f"SERIAL x{n}", srows, serial_total)

        # 3. Concurrent — the moment to watch LM Studio
        print(f"\n>>> FIRING {n} CONCURRENT REQUESTS NOW — watch LM Studio <<<")
        await asyncio.sleep(1.5)
        t0 = time.perf_counter()
        crows = await _concurrent(client, args.model, prompts)
        conc_total = time.perf_counter() - t0
        _report(f"CONCURRENT x{n}", crows, conc_total)

        # 4. Verdict
        print("\n================= VERDICT =================")
        if serial_total:
            speedup = serial_total / conc_total if conc_total else 0
            print(f"serial x{n}: {serial_total:.1f}s   concurrent x{n}: {conc_total:.1f}s   "
                  f"speedup: {speedup:.2f}x  (ideal ~{n}x)")
            if speedup >= n * 0.6:
                print("=> LM Studio IS running the requests in PARALLEL. Fan-out is real.")
            elif speedup <= 1.3:
                print("=> LM Studio is SERIALIZING the requests (no meaningful speedup). "
                      "parallel_research gives ~no gain until concurrency is enabled "
                      "(LM Studio server: raise max concurrent requests / enable parallel).")
            else:
                print("=> PARTIAL parallelism (some batching, not full). Worth raising LM Studio's "
                      "concurrency limit.")
        else:
            per = [r[2] for r in crows]
            print(f"concurrent x{n}: wall {conc_total:.1f}s; slowest single {max(per):.1f}s. "
                  f"If wall ~= slowest single => parallel; if wall ~= sum => serialized "
                  f"(sum={sum(per):.1f}s).")


if __name__ == "__main__":
    asyncio.run(main())
