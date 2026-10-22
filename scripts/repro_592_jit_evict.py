"""#592 reproduction: does LM Studio unload a model that is still LOADING?

Talks to LM Studio directly — no Brain, no Chad, no eval harness — so the race is
provable without anything of ours in the way.

Hypothesis (refs lmstudio-bug-tracker#671 + the Auto-Evict docs): LM Studio's
"Only Keep Last JIT Loaded Model" unloads the previous **JIT-loaded** model the instant a
new one is JIT-requested, without waiting for it to finish loading or generating. Models
made resident through the explicit ``POST /api/v1/models/load`` API are "manually loaded"
and documented as immune.

Each scenario: start making the 9B resident, then ~1s later (while it is still loading)
fire an inference request for the big model, and see whether the 9B survives.

  A  9B loaded EXPLICITLY  + big model JIT   -> if immune, 9B survives
  B  9B loaded via JIT     + big model JIT   -> if the hypothesis holds, 9B is killed

Run it again after each LM Studio settings toggle and compare the verdicts:

    python scripts/repro_592_jit_evict.py
    python scripts/repro_592_jit_evict.py --label "engine-protocol OFF"
"""
from __future__ import annotations

import argparse
import asyncio
import time
from typing import Dict, List, Optional, Tuple

import httpx

BASE = "http://localhost:1234"
SMALL = "qwen/qwen3.5-9b"
BIG = "google/gemma-4-26b-a4b-qat"

# Long enough to cover a ~50s cold load of the big model without masking a real hang.
LOAD_TIMEOUT = 300.0
T0 = time.monotonic()


def log(msg: str) -> None:
    print(f"[{time.monotonic() - T0:6.1f}s] {msg}", flush=True)


async def loaded_models(client: httpx.AsyncClient) -> List[str]:
    """Resident models — `loaded_instances`, NOT /v1/models (which lists all downloaded)."""
    try:
        r = await client.get(f"{BASE}/api/v1/models", timeout=10.0)
        return [
            m["key"] for m in r.json().get("models", [])
            if isinstance(m, dict) and m.get("key") and m.get("loaded_instances")
        ]
    except Exception as e:
        log(f"  ! could not list models: {e}")
        return []


async def unload_all(client: httpx.AsyncClient) -> None:
    for key in await loaded_models(client):
        try:
            await client.post(f"{BASE}/api/v1/models/unload",
                              json={"instance_id": key}, timeout=60.0)
            log(f"  unloaded {key}")
        except Exception as e:
            log(f"  ! unload {key} failed: {e}")
    # Give LM Studio a moment to actually release the instances before the next load.
    await asyncio.sleep(3.0)


async def explicit_load(client: httpx.AsyncClient, model: str) -> Tuple[str, float]:
    """POST /api/v1/models/load — blocks until ready. Returns (outcome, seconds)."""
    t = time.monotonic()
    try:
        r = await client.post(f"{BASE}/api/v1/models/load",
                              json={"model": model}, timeout=LOAD_TIMEOUT)
        dt = time.monotonic() - t
        if r.status_code == 200:
            return "ok", dt
        return f"HTTP {r.status_code}: {r.text[:120]}", dt
    except Exception as e:
        return f"{type(e).__name__}: {e}", time.monotonic() - t


async def jit_completion(client: httpx.AsyncClient, model: str,
                         max_tokens: int = 1) -> Tuple[str, float]:
    """A plain chat completion. For a non-resident model this is what JIT-loads it."""
    t = time.monotonic()
    try:
        r = await client.post(
            f"{BASE}/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": "hi"}],
                  "max_tokens": max_tokens, "temperature": 0},
            timeout=LOAD_TIMEOUT,
        )
        dt = time.monotonic() - t
        if r.status_code == 200:
            return "ok", dt
        return f"HTTP {r.status_code}: {r.text[:160]}", dt
    except Exception as e:
        return f"{type(e).__name__}: {e}", time.monotonic() - t


async def scenario(client: httpx.AsyncClient, name: str, small_via: str) -> Dict[str, object]:
    log(f"=== {name} : 9B via {small_via.upper()}, big model JIT 1s later ===")
    await unload_all(client)

    if small_via == "explicit":
        small_task = asyncio.create_task(explicit_load(client, SMALL))
    else:
        small_task = asyncio.create_task(jit_completion(client, SMALL))

    # Fire the competing request while the 9B is still loading — that is the whole point.
    await asyncio.sleep(1.0)
    log(f"  -> firing JIT inference for {BIG} while {SMALL} is still loading")
    big_task = asyncio.create_task(jit_completion(client, BIG))

    small_result, small_s = await small_task
    log(f"  {SMALL} finished: {small_result} ({small_s:.1f}s)")
    big_result, big_s = await big_task
    log(f"  {BIG} finished: {big_result} ({big_s:.1f}s)")

    resident = await loaded_models(client)
    survived = any(SMALL.split("/")[-1] in m for m in resident)
    log(f"  resident now: {resident or 'none'}")
    log(f"  VERDICT: 9B {'SURVIVED' if survived else 'WAS KILLED'}\n")

    return {"scenario": name, "small_via": small_via, "small_result": small_result,
            "small_s": round(small_s, 1), "big_result": big_result,
            "big_s": round(big_s, 1), "resident_after": resident, "survived": survived}


async def main(label: Optional[str]) -> None:
    async with httpx.AsyncClient() as client:
        try:
            await client.get(f"{BASE}/api/v1/models", timeout=5.0)
        except Exception as e:
            print(f"LM Studio unreachable at {BASE}: {e}")
            return

        print(f"\n#592 JIT/auto-evict reproduction{f'  [{label}]' if label else ''}")
        print(f"small={SMALL}  big={BIG}\n")

        results = [
            await scenario(client, "A  explicit-load 9B", "explicit"),
            await scenario(client, "B  JIT-load 9B", "jit"),
        ]

        print("=" * 78)
        print(f"SUMMARY{f'  [{label}]' if label else ''}")
        print(f"{'scenario':<24}{'9B survived':<14}{'9B outcome'}")
        for r in results:
            print(f"{str(r['scenario']):<24}{('YES' if r['survived'] else 'NO'):<14}"
                  f"{str(r['small_result'])[:44]}")
        print("=" * 78)
        print("Expected if the Auto-Evict hypothesis holds: A=YES (explicit loads are\n"
              "immune), B=NO (JIT-loaded models get evicted mid-load).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", help="tag the run, e.g. 'auto-evict OFF'")
    args = ap.parse_args()
    asyncio.run(main(args.label))
