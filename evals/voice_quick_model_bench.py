"""voice_quick_model_bench.py — benchmark a minimal-context 9B tool-call as the voice
fast-lane's fallback tier, instead of trying to out-guess phrasing with more regex/
embedding-similarity rules (issue #654 problem 3).

Live testing found the regex+embedding fast lane (`voice_fast_lane.py`) missed real
phrasing outright — "can you open youtube" doesn't match a `^open ` anchored regex,
and the embedding tier scored below threshold even for the exact canonical phrase
("open youtube" itself scored higher for "media" than "open"). Rather than patch the
matcher with more special cases, this measures the alternative: send the SAME fast
local model already resident for voice (`qwen/qwen3.5-9b`) a request with NOTHING but
a minimal system prompt and 3 tool schemas — no persona, no history, no memory, no
RAG shortlist — and see how fast and how accurate real tool-calling is.

This talks to LM Studio directly (same minimal raw-HTTP pattern as
`src/core/tts/dynamic_filler.py`) — no server, no container, no MCP fleet. Requires
LM Studio running with `qwen/qwen3.5-9b` available.

Run:
    python -m evals.voice_quick_model_bench
    python -m evals.voice_quick_model_bench --model qwen/qwen3-14b   # compare a model
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DATASET_PATH = Path(__file__).parent / "datasets" / "voice_quick_model.jsonl"
DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "qwen/qwen3.5-9b"

# Keep in sync with src/core/orchestration/nodes/voice_quick_tool.py
from src.core.orchestration.nodes.voice_quick_tool import SYSTEM_PROMPT, TOOLS


def load_cases() -> List[Dict[str, Any]]:
    cases = []
    with DATASET_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def call_model(query: str, *, model: str, base_url: str, timeout: float) -> Dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 200,
    }
    if "qwen3" in model.lower():
        payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"error": str(e), "wall_ms": (time.perf_counter() - t0) * 1000}
    wall_ms = (time.perf_counter() - t0) * 1000

    message = body.get("choices", [{}])[0].get("message", {})
    tool_calls = message.get("tool_calls") or []
    parsed_calls = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_raw": fn.get("arguments")}
        parsed_calls.append({"name": fn.get("name"), "args": args})

    return {
        "wall_ms": wall_ms,
        "content": message.get("content"),
        "tool_calls": parsed_calls,
        "usage": body.get("usage", {}),
    }


def run_bench(cases: List[Dict[str, Any]], *, model: str, base_url: str, timeout: float) -> List[Dict[str, Any]]:
    results = []
    for case in cases:
        r = call_model(case["query"], model=model, base_url=base_url, timeout=timeout)
        expect_tool = case.get("expect_tool")
        calls = r.get("tool_calls") or []
        got_tool = calls[0]["name"] if calls else None
        got_args = calls[0]["args"] if calls else {}

        if expect_tool is None:
            correct = got_tool is None
        else:
            arg_val = " ".join(str(v) for v in got_args.values()).lower()
            correct = (
                got_tool == expect_tool
                and case.get("expect_arg_contains", "").lower() in arg_val
            )

        results.append({
            "id": case["id"],
            "query": case["query"],
            "expect_tool": expect_tool,
            "got_tool": got_tool,
            "got_args": got_args,
            "wall_ms": round(r.get("wall_ms", 0), 1),
            "error": r.get("error"),
            "correct": correct,
        })
    return results


def print_table(results: List[Dict[str, Any]]) -> None:
    print(f"\n{'id':<26} {'expect':<14} {'got':<14} {'args':<28} {'ms':>7}  ok")
    for r in results:
        args_str = json.dumps(r["got_args"])[:26] if r["got_args"] else ""
        ok = "OK" if r["correct"] else "MISS"
        err = f" ERROR={r['error']}" if r["error"] else ""
        print(f"{r['id']:<26} {str(r['expect_tool']):<14} {str(r['got_tool']):<14} "
              f"{args_str:<28} {r['wall_ms']:>7.0f}  {ok}{err}")

    times = [r["wall_ms"] for r in results if not r["error"]]
    accuracy = sum(r["correct"] for r in results) / len(results)
    print(f"\naccuracy: {accuracy*100:.0f}% ({sum(r['correct'] for r in results)}/{len(results)})")
    if times:
        print(f"latency: mean={statistics.mean(times):.0f}ms median={statistics.median(times):.0f}ms "
              f"min={min(times):.0f}ms max={max(times):.0f}ms")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    cases = load_cases()
    print(f"Loaded {len(cases)} cases. model={args.model} base_url={args.base_url}")
    results = run_bench(cases, model=args.model, base_url=args.base_url, timeout=args.timeout)
    print_table(results)


if __name__ == "__main__":
    main()
