"""
Diagnostic Script: Verify Server Startup Sequence and Tool Discovery
This script systematically runs the startup sequence step-by-step with precise timers
and timeouts to verify that all blocks have been successfully resolved and starts instantly.
"""

import sys
import asyncio
import time
import logging

# Set selector policy on Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from pathlib import Path
root_dir = str(Path(__file__).parents[1])
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Diagnostic")

async def run_step(name, coro_or_func, timeout=10.0):
    print(f"\n>>> [DIAG] Step: {name} (timeout={timeout}s)...")
    start = time.time()
    try:
        if asyncio.iscoroutinefunction(coro_or_func) or asyncio.iscoroutine(coro_or_func):
            if asyncio.iscoroutine(coro_or_func):
                res = await asyncio.wait_for(coro_or_func, timeout=timeout)
            else:
                res = await asyncio.wait_for(coro_or_func(), timeout=timeout)
        else:
            # Synchronous function
            res = coro_or_func()
        elapsed = time.time() - start
        print(f"[ SUCCESS ] '{name}' completed in {elapsed:.2f}s!")
        return True, elapsed
    except asyncio.TimeoutError:
        print(f"[!! HANG DETECTED !!] '{name}' timed out after {timeout}s.")
        return False, timeout
    except Exception as e:
        elapsed = time.time() - start
        print(f"[ FAILED ] '{name}' failed after {elapsed:.2f}s with error: {e}")
        return False, elapsed

async def main():
    print("=" * 60)
    print("CHAD SERVER STARTUP DIAGNOSTIC RUN")
    print("=" * 60)

    results = {}

    # Step 1: Import ToolRegistry
    def import_registry():
        from src.infrastructure.tools import ToolRegistry
        return ToolRegistry
    ok, elapsed = await run_step("Import ToolRegistry", import_registry)
    results["Import ToolRegistry"] = (ok, elapsed)

    # Step 2: Instantiate ToolRegistry
    from src.infrastructure.tools import ToolRegistry
    ok, elapsed = await run_step("Instantiate ToolRegistry()", lambda: ToolRegistry())
    results["Instantiate ToolRegistry()"] = (ok, elapsed)
    if ok:
        registry = ToolRegistry()
    else:
        print("[CRITICAL] Could not instantiate ToolRegistry. Aborting.")
        return

    # Step 3: Run discover_all
    ok, elapsed = await run_step(
        "Run discover_all() (Filesystem Scanner)", 
        asyncio.to_thread(registry.discover_all, sync_cloud=False),
        timeout=15.0
    )
    results["Run discover_all()"] = (ok, elapsed)

    # Step 4: Verify Semantic Selector instantiation
    ok, elapsed = await run_step(
        "Verify Semantic Selector instantiation",
        lambda: registry._selector
    )
    results["Verify Semantic Selector instantiation"] = (ok, elapsed)

    # Step 5: Test Semantic model lazy probe
    selector = registry._selector
    ok, elapsed = await run_step(
        "Probe Semantic Selector _ensure_model()",
        lambda: selector._ensure_model(),
        timeout=10.0
    )
    results["Probe Semantic Selector _ensure_model()"] = (ok, elapsed)

    # Step 6: Test skill_loader.initialize()
    from src.core.skill_system.skill_loader import get_skill_loader
    skill_loader = get_skill_loader()
    ok, elapsed = await run_step(
        "Run skill_loader.initialize() (Supabase query or local scan)",
        skill_loader.initialize(),
        timeout=10.0
    )
    results["Run skill_loader.initialize()"] = (ok, elapsed)

    print("\n" + "=" * 60)
    print("DIAGNOSTIC SUMMARY REPORT")
    print("=" * 60)
    all_ok = True
    for step, (status, elapsed_time) in results.items():
        status_str = "PASS" if status else "FAIL/HANG"
        if not status:
            all_ok = False
        print(f" - {step:<50}: {status_str:<10} ({elapsed_time:.2f}s)")
    print("=" * 60)
    
    if all_ok:
        print("VERIFICATION COMPLETED: All startup components are perfectly unblocked!")
    else:
        print("VERIFICATION WARNING: Some components are still blocking or failing.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
