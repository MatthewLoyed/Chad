"""
Live safeguard check for src/core/intelligence/model_lifecycle.py against a REAL LM Studio
instance. tests/unit/test_model_lifecycle.py mocks get_loaded_model_ids/load_model/unload_model
entirely, so it proves the coordinator's *logic* is correct but can never catch LM Studio's own
documented failure mode: concurrent JIT load requests for the same model spinning up a second
instance (see docs/features/MODEL_LIFECYCLE.md, "Why this exists"). This script exercises the
coordinator against the real /api/v1/models endpoint, which is the only place a duplicate
instance would actually show up.

Requires LM Studio running and reachable. Point it at a DEV instance
(scripts/prod/launch_dev.ps1, port 8788's LM Studio, or your own) — never run this against the
production Brain's LM Studio while a real conversation might be active; it loads/unloads models
and will fight a live turn for residency.

Usage:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe tests\manual\verify_model_lifecycle.py
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe tests\manual\verify_model_lifecycle.py --base-url http://localhost:1234/v1 --model qwen/qwen3.5-9b
"""
import argparse
import asyncio
import sys
import time
from typing import List

sys.path.append(".")

from src.core.intelligence import model_lifecycle as ml
from src.core.intelligence.model_swap import get_loaded_model_ids

PASS = "[PASS]"
FAIL = "[FAIL]"


async def _instance_count(base_url: str, model_id: str) -> int:
    """Count loaded_instances for model_id via the native (non-OpenAI-compat) endpoint —
    the OpenAI-compat /v1/models list only shows one row per model regardless of how many
    instances are actually resident, so this is the only reliable duplicate-instance check."""
    import httpx

    api_root = base_url.replace("/v1", "").rstrip("/")
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{api_root}/api/v1/models")
        if resp.status_code != 200:
            return -1
        body = resp.json()
    for entry in body.get("models", []):
        if not isinstance(entry, dict):
            continue
        if not ml._same_model(str(entry.get("key", "")), model_id):
            continue
        return len(entry.get("loaded_instances") or [])
    return 0


async def check_no_duplicate_instance_under_concurrency(base_url: str, model_id: str) -> bool:
    """Simulates the exact race this module was built to close: intent router, tool selector,
    and main-model preload all claiming the same model within the same turn."""
    print(f"\n--- Concurrency check: 5 simultaneous ensure_resident() callers for '{model_id}' ---")
    owners = ["intent_router", "tool_selector", ml.OWNER_MAIN_MODEL, "scout_secondary", "handoff_swap"]
    t0 = time.monotonic()
    results = await asyncio.gather(*[
        ml.ensure_resident(base_url, model_id, owner=o) for o in owners
    ])
    elapsed = time.monotonic() - t0
    print(f"  ensure_resident() results: {results} ({elapsed:.1f}s)")

    count = await _instance_count(base_url, model_id)
    print(f"  loaded_instances for '{model_id}': {count}")
    ok = all(results) and count == 1
    print(f"  {PASS if ok else FAIL}: expected exactly 1 instance, got {count}")

    for o in owners:
        await ml.release(base_url, model_id, owner=o)
    return ok


async def check_active_model_never_evicted(base_url: str, model_id: str, filler_model_id: str) -> bool:
    """Marks model_id active, then forces a load of a second model with a tight VRAM budget
    (via config monkeypatch is not available live, so this just documents the expectation —
    real budget eviction needs local_models.lifecycle.vram_budget_gb configured tightly enough
    to force a choice). This check verifies mark_active/mark_inactive plumbing works against
    the real server and the model stays listed as loaded throughout."""
    print(f"\n--- Active-model protection: '{model_id}' marked active while loading '{filler_model_id}' ---")
    await ml.ensure_resident(base_url, model_id, owner="verify_script")
    await ml.mark_active(base_url, model_id)

    await ml.ensure_resident(base_url, filler_model_id, owner="verify_script_filler")

    loaded = await get_loaded_model_ids(base_url)
    still_present = any(ml._same_model(m, model_id) for m in loaded)
    print(f"  '{model_id}' still loaded after unrelated ensure_resident(): {still_present}")
    print(f"  {PASS if still_present else FAIL}")

    await ml.mark_inactive(base_url, model_id)
    await ml.release(base_url, model_id, owner="verify_script")
    await ml.release(base_url, filler_model_id, owner="verify_script_filler")
    return still_present


async def check_diagnostics_snapshot(base_url: str) -> None:
    print("\n--- Coordinator diagnostics snapshot ---")
    print(f"  {ml.get_diagnostics(base_url)}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default=None, help="Model id to stress-test (defaults to config's tools.llm_selector_model)")
    parser.add_argument("--filler-model", default=None, help="A second, smaller model id to load alongside --model")
    args = parser.parse_args()

    from src.shared.config_manager import ConfigManager
    model_id = args.model or ConfigManager.get("tools.llm_selector_model", "qwen/qwen3.5-9b")
    filler_model_id = args.filler_model or ConfigManager.get("intent_router.model", "google/gemma-3-1b")

    print(f"Target LM Studio: {args.base_url}")
    print(f"Primary model:    {model_id}")
    print(f"Filler model:     {filler_model_id}")

    loaded = await get_loaded_model_ids(args.base_url)
    print(f"\nCurrently loaded (OpenAI-compat listing): {loaded}")
    if not loaded and not any([model_id, filler_model_id]):
        print("Could not reach LM Studio — aborting.")
        return 1

    results: List[bool] = []
    results.append(await check_no_duplicate_instance_under_concurrency(args.base_url, model_id))
    results.append(await check_active_model_never_evicted(args.base_url, model_id, filler_model_id))
    await check_diagnostics_snapshot(args.base_url)

    print(f"\n=== {sum(results)}/{len(results)} checks passed ===")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
