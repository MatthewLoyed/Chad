"""
Live verification for epic #450 / issue #500 acceptance criterion:
"Multi-tool turn completes on Ollama with no LM Studio installed."

Drives LiteLLMAdapter directly against a real Ollama instance — the same adapter class
production uses via ModelRouter — without touching config/preferences.yaml (which drives the
live prod Chad instance on :8767). This proves the request/response path (tool schema
conversion, streaming, tool_calls parsing, provider_capabilities gating) works end-to-end for
Ollama, independent of the running server.

Requires Ollama serving locally with a tool-capable model pulled.

Usage:
    $env:PYTHONPATH="."; .venv\Scripts\python.exe tests\manual\verify_ollama_e2e.py
    $env:PYTHONPATH="."; .venv\Scripts\python.exe tests\manual\verify_ollama_e2e.py --model qwen3.5:4b --base-url http://localhost:11434/v1
"""
import argparse
import asyncio
import sys

sys.path.append(".")

from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter

PASS = "[PASS]"
FAIL = "[FAIL]"

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, e.g. 'Paris'"},
        },
        "required": ["city"],
    },
}


async def run(base_url: str, model: str, api_key: str) -> bool:
    adapter = LiteLLMAdapter(
        model_name=model,
        api_key=api_key,
        base_url=base_url,
        system_instruction="You are a helpful assistant. Use tools when appropriate.",
        supports_tools=True,
    )

    messages = [
        {"role": "user", "content": "What's the weather like in Tokyo right now? Use the get_weather tool."}
    ]

    print(f"--- Turn 1: requesting tool call from {model} @ {base_url} ---")
    resp = await adapter.generate(messages, tools=[WEATHER_TOOL], temperature=0.2)
    print(f"  finish_reason={resp.finish_reason} text={resp.text!r}")
    print(f"  tool_calls={resp.tool_calls}")

    ok_tool_call = bool(resp.tool_calls) and any(tc.get("name") == "get_weather" for tc in resp.tool_calls)
    print(f"  {PASS if ok_tool_call else FAIL}: model emitted get_weather tool call")
    if not ok_tool_call:
        return False

    tc = resp.tool_calls[0]
    tool_result_msg = adapter.build_tool_result_message(tc["id"], tc["name"], {"city": tc["args"].get("city", "Tokyo"), "temp_f": 68, "condition": "cloudy"})
    assistant_msg = adapter.build_assistant_tool_call_message(resp.tool_calls, resp.text)
    messages.extend([assistant_msg, tool_result_msg])

    print("\n--- Turn 2: feeding tool result back, expecting natural-language answer ---")
    resp2 = await adapter.generate(messages, tools=[WEATHER_TOOL], temperature=0.2)
    print(f"  finish_reason={resp2.finish_reason} text={resp2.text!r}")

    ok_followup = resp2.finish_reason == "stop" and bool(resp2.text) and "68" in resp2.text or "cloudy" in (resp2.text or "").lower()
    print(f"  {PASS if ok_followup else FAIL}: model produced a natural-language answer referencing the tool result")

    return ok_tool_call and ok_followup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--api-key", default="ollama")
    args = parser.parse_args()

    ok = asyncio.run(run(args.base_url, args.model, args.api_key))
    print(f"\n{PASS if ok else FAIL}: overall Ollama multi-tool turn verification")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
