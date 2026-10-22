r"""
Live intent-router probe — calls the real Gemma model in LM Studio.

Usage:
  $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.intent_router_live
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

from src.core.intelligence.intent_mode_router import IntentModeRouter
from src.shared.turn_mode import normalize_turn_mode

MAX_WORKERS = 8


def _load_cases(path: Path) -> List[Dict[str, Any]]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


def run_live(dataset: Path) -> int:
    router = IntentModeRouter.from_config()
    if router is None:
        print("FAIL: intent_router.enabled is false or config missing")
        return 1

    if not router.warmup():
        print(f"FAIL: router warmup failed — {router.last_meta}")
        return 1

    cases = _load_cases(dataset)
    if not cases:
        print(f"FAIL: no cases in {dataset}")
        return 1

    print(f"Intent router live probe — model={router._model_id}, cases={len(cases)}\n")
    passed = 0
    failed = 0
    t0 = time.perf_counter()

    # classify() is a blocking network call (urllib) against one already-resident, cheap
    # model — LM Studio serves these fine concurrently, so a thread pool turns a ~2min
    # sequential run into a few seconds instead of paying each round-trip one at a time.
    def _run_case(case: Dict[str, Any]):
        query = case["query"]
        expected = normalize_turn_mode(case["expect_mode"])
        result = router.classify(query)
        return case, expected, result

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(cases))) as pool:
        for case, expected, result in pool.map(_run_case, cases):
            case_id = case.get("id", case.get("query", "?"))
            query = case["query"]
            ok = result.mode == expected
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed += 1
            print(
                f"  [{status}] {case_id}: {query!r}\n"
                f"         expected={expected} got={result.mode} "
                f"confidence={result.confidence:.2f} latency={result.latency_s:.2f}s"
            )
            if not ok and result.raw:
                print(f"         raw={result.raw[:120]!r}")

    elapsed = time.perf_counter() - t0
    print(f"\nResult: {passed}/{len(cases)} passed, {failed} failed ({elapsed:.1f}s total)")
    return 0 if failed == 0 else 1


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live Gemma intent-router classification probe")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/datasets/intent_router.jsonl"),
        help="JSONL file with query + expect_mode per line",
    )
    args = parser.parse_args(argv)
    return run_live(args.dataset)


if __name__ == "__main__":
    sys.exit(main())
