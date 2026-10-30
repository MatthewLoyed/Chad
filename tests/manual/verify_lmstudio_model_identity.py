"""Explicit live acceptance: reproduce two GPT-OSS instances, then prove one.

Stop the dev stack first. This unloads ONLY the two named GPT-OSS instances.
Run with PYTHONPATH=. and --live --lms <lms executable>. No user prompts/data.
"""
import argparse
import asyncio
import json
import subprocess
from pathlib import Path

import httpx
import litellm

from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
from src.core.intelligence.endpoint_contract import normalize_model_name
from src.core.intelligence.providers import LMStudioLifecycle


async def verify(args):
    base = "http://localhost:1234/v1"
    native = "openai/gpt-oss-20b"
    report = {}
    litellm.callbacks = []
    litellm.success_callback = []
    litellm.failure_callback = []

    def snapshot():
        models = json.loads(subprocess.check_output([args.lms, "ps", "--json"], text=True))
        return [{k: m.get(k) for k in ("identifier", "modelKey", "status", "contextLength")}
                for m in models if "gpt-oss-20b" in m.get("modelKey", "")]

    def reset():
        models = snapshot()
        if any(m["status"] != "idle" for m in models):
            raise RuntimeError("GPT-OSS is busy; wait for the current turn to finish")
        for m in models:
            subprocess.run([args.lms, "unload", m["identifier"]], check=True, capture_output=True)

    messages = [{"role": "user", "content": "Reply with OK."}]
    provider = LMStudioLifecycle(base)
    reset()
    assert await provider.load(native, context_length=32000)
    report["before_inference"] = snapshot()
    # The exact original path: management uses the native key; LiteLLM strips
    # openai/ from the inference name and JIT-loads the legacy alias.
    await litellm.acompletion(model=normalize_model_name(native, base),
                             api_base=base, api_key="lm-studio", messages=messages,
                             max_tokens=16, stream=False, timeout=180)
    report["broken_after_inference"] = snapshot()
    assert len(report["broken_after_inference"]) == 2, report
    print(json.dumps({"reproduced": report}, indent=2), flush=True)

    reset()
    assert await provider.load(native, context_length=32000)
    adapter = LiteLLMAdapter(native, base_url=base, api_key="lm-studio")
    adapter._apply_gate_routing = lambda request: None
    for _ in range(2):
        response = await adapter.generate(messages, max_tokens=16)
        assert response.finish_reason != "error", response
    report["fixed_after_two_inferences"] = snapshot()
    assert [m["identifier"] for m in report["fixed_after_two_inferences"]] == [native], report
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--lms", required=True)
    parser.add_argument("--report", default="data/dev_logs/model_identity_acceptance.json")
    asyncio.run(verify(parser.parse_args()))
