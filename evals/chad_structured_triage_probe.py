"""Bounded direct-model transport check, never a behavioral replay or coding dispatch.

Requires an already loaded model; saves exact request/response and residency metadata.
No automatic loading, retries, output repair, or extraction from reasoning content.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from evals.chad_on_chad_driver import _diagnosis_prompt
from evals.chad_triage_gate import triage_json_schema, validate_triage


def build_request(model: str, case: dict, trace: dict, max_tokens: int = 1800) -> dict:
    if not 1 <= max_tokens <= 1800:
        raise ValueError("Use a bounded output budget of 1–1800 tokens")
    return {
        "model": model, "messages": [{"role": "user", "content":
            _diagnosis_prompt(case, trace) + "\nNo source code was supplied. Recommend investigate "
            "if source inspection is needed. Keep observations and hypotheses concise."}],
        "temperature": 0.6, "max_tokens": max_tokens, "stream": False,
        # OpenAI-compatible endpoint uses 'none'; native /api/v1/chat uses 'off'.
        "reasoning_effort": "none",
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "chad_triage_v1", "strict": True, "schema": triage_json_schema()}},
    }


def assess_response(data: dict, trace: dict, policy: dict) -> dict:
    choice = data["choices"][0]
    message = choice["message"]
    content = message.get("content") or ""
    reasoning_tokens = (data.get("usage", {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
    complete = choice.get("finish_reason") == "stop" and bool(content.strip()) and not message.get("tool_calls")
    no_reasoning = reasoning_tokens == 0 and not message.get("reasoning_content")
    gate = validate_triage(content, trace, policy)
    return {"final_answer_complete": complete, "reasoning_control_observed": no_reasoning,
            "reasoning_tokens": reasoning_tokens, "gate": gate,
            "transport_passed": complete and no_reasoning and gate["valid"],
            "coding_dispatched": False}


def run_probe(case: dict, trace: dict, *, model: str, output: Path,
              base_url: str = "http://localhost:1234", timeout_s: float = 120) -> dict:
    def get_models():
        with urllib.request.urlopen(base_url + "/api/v1/models", timeout=10) as response:
            return json.load(response)["models"]

    result = {"kind": "direct_model_control_check", "coding_dispatched": False}
    start = time.monotonic()
    try:
        models = get_models()
        result["initial_residency"] = [m for m in models if m.get("loaded_instances")]
        loaded = {i["id"] for m in models for i in m.get("loaded_instances", [])}
        if model not in loaded:
            raise ValueError("Requested model is not already loaded; residency coordination required")
        body = build_request(model, case, trace)
        result.update(request=body, trace=trace, policy=case.get("triage_policy") or {})
        req = urllib.request.Request(base_url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        generation_start = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            result["response"] = json.load(response)
        result["generation_s"] = round(time.monotonic() - generation_start, 3)
        result.update(assess_response(result["response"], trace, result["policy"]))
    except urllib.error.HTTPError as exc:
        result["error"] = f"HTTP {exc.code}: {exc.read().decode(errors='replace')}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["elapsed_s"] = round(time.monotonic() - start, 3)
        try:
            result["final_residency"] = [m for m in get_models() if m.get("loaded_instances")]
        except Exception as exc:
            result["residency_error"] = str(exc)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Local JSON with case and trace fields")
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="google/gemma-4-26b-a4b-qat")
    args = parser.parse_args()
    saved = json.loads(args.input.read_text(encoding="utf-8"))
    result = run_probe(saved["case"], saved["trace"], model=args.model, output=args.output)
    print(json.dumps({key: result.get(key) for key in
                     ("transport_passed", "final_answer_complete", "reasoning_control_observed", "generation_s", "error")}))
