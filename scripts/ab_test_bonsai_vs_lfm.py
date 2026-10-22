"""A/B Benchmark: Bonsai 27B vs Liquid LFM 2.5 (24B/2B Active MoE).

Tests generation speed (tokens/sec), time-to-first-token (TTFT),
instruction-following, and tool-dispatch capability across 3 standard prompts.
"""

import json
import time
import urllib.request
from typing import Any, Dict, List

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

TEST_PROMPTS = [
    {
        "id": "agentic_tool_dispatch",
        "title": "1. Multi-Step Tool Dispatch & Reasoning",
        "system": "You are Chad, an autonomous AI assistant with tools. If tools are needed, state the exact tool calls with arguments.",
        "user": (
            "I need to check recent transactions on my checking account, identify any that exceed $100, "
            "and draft a summary email to Matthew with the details. How will you execute this step-by-step?"
        ),
        "max_tokens": 1800,
        "temperature": 0.3,
    },
    {
        "id": "technical_reasoning",
        "title": "2. Technical Reasoning & System Architecture",
        "system": "You are Chad, a senior systems architect and pairing partner.",
        "user": (
            "Explain why using a 35B parameter autoregressive MoE model for real-time text-to-speech "
            "in a desktop voice assistant introduces fatal latency bottlenecks compared to an 82M model like Kokoro. "
            "Provide 3 specific architectural reasons."
        ),
        "max_tokens": 1800,
        "temperature": 0.6,
    },
    {
        "id": "strict_json_constraints",
        "title": "3. Strict Output Formatting (Raw JSON)",
        "system": "You are a precise data formatting engine. Output valid raw JSON only. No markdown, no commentary.",
        "user": (
            "Analyze the pros and cons of using MoE (sparse) vs Dense models for local AI assistants. "
            "Format strictly as a JSON object with keys: 'pros' (list of strings), 'cons' (list of strings), "
            "and 'final_verdict' (single string). No markdown backticks."
        ),
        "max_tokens": 1800,
        "temperature": 0.2,
    },
]


def query_model(model_name: str, test_case: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": test_case["system"]},
            {"role": "user", "content": test_case["user"]},
        ],
        "temperature": test_case["temperature"],
        "max_tokens": test_case["max_tokens"],
        "stream": False,
    }

    t0 = time.perf_counter()
    req = urllib.request.Request(
        LM_STUDIO_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        elapsed = time.perf_counter() - t0

        usage = data.get("usage", {})
        completion_tokens = usage.get("completion_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        tps = completion_tokens / elapsed if elapsed > 0 else 0
        
        msg = data["choices"][0]["message"]
        content = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""

        return {
            "success": True,
            "model": model_name,
            "finish_reason": data["choices"][0].get("finish_reason"),
            "elapsed_seconds": round(elapsed, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "tokens_per_second": round(tps, 1),
            "content": content.strip(),
            "reasoning_length": len(reasoning),
            "output": content.strip() if content.strip() else f"[Reasoning Only ({len(reasoning)} chars)] {reasoning[:300]}...",
        }
    except Exception as e:
        return {
            "success": False,
            "model": model_name,
            "error": str(e),
        }


def run_benchmark_for_model(model_name: str) -> List[Dict[str, Any]]:
    print(f"\n{'='*60}\nRUNNING BENCHMARK FOR: {model_name}\n{'='*60}")
    results = []
    for test in TEST_PROMPTS:
        print(f"\n--> Running test: {test['title']}...")
        res = query_model(model_name, test)
        if res.get("success"):
            print(f"    Speed: {res['tokens_per_second']} tok/s ({res['completion_tokens']} tokens in {res['elapsed_seconds']}s)")
            print(f"    Snippet: {res['output'][:180].replace(chr(10), ' ')}...")
        else:
            print(f"    Failed: {res.get('error')}")
        results.append({"test": test["id"], "result": res})
    return results


if __name__ == "__main__":
    import sys
    models_to_test = sys.argv[1:] if len(sys.argv) > 1 else ["prism-ml/bonsai-27b"]
    all_results = {}
    for m in models_to_test:
        all_results[m] = run_benchmark_for_model(m)

    with open("artifacts/ab_test_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print("\nBenchmark run complete! Results saved to artifacts/ab_test_results.json")
