r"""
Probe for the Gemini Notebook + Canvas coursework planning prompt.
Measures:
1. Supervisor intent decomposition: Does it route to canvas_agent vs browser_agent?
2. Browser observation sanitization: How does the model react when landing on a parked domain?
3. Protocol normalization: Verify argument parsing cleans token bleed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
from src.shared.config_manager import ConfigManager
from src.core.orchestration.page_surface import PageSurface, classify_page_surface, format_surface_banner

TEST_PROMPT = (
    "create a plan for going to gemini notebook website which has my wireless networks course "
    "notebook and add any new lectures from canvas course wireless networks that arent in the notebook already. "
    "this will involve looking at both the gemini notebook files in the browser to see whats in there, "
    "and cross referencing with the total pdfs lectures from the canvas course using hte canvas subagent"
)

async def probe_supervisor_routing():
    print("=" * 60)
    print("TEST 1: Supervisor Specialist Routing on Compound Prompt")
    print("=" * 60)

    from src.core.orchestration.handoffs import create_handoff_tools
    from src.infrastructure.tools.web_search_routing import BROWSER_AGENT_HANDOFF_DESC

    # Minimal mock agents dict to build handoff tools
    dummy_agents = {
        "browser_agent": None,
        "canvas_agent": None,
        "system_agent": None,
        "traversal_agent": None,
    }
    tools = create_handoff_tools(dummy_agents)
    schemas = [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.args_schema.schema() if hasattr(t, "args_schema") and t.args_schema else {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "The task brief."}},
                "required": ["task"],
            }
        }
        for t in tools
    ]

    provider_cfg = ConfigManager.get_local_provider_config("lmstudio") or {}
    model_name = "prism-ml/bonsai-27b"
    adapter = LiteLLMAdapter(
        model_name=model_name,
        base_url=provider_cfg.get("base_url", "http://localhost:1234/v1"),
        api_key=provider_cfg.get("api_key", "lm-studio"),
    )

    system_prompt = (
        "You are Chad, an AI orchestrator. Decompose and coordinate the user's request. "
        "Choose the most appropriate specialist agent tool for each part of the task.\n"
        "- delegate_to_canvas_agent: Canvas LMS coursework, syllabus, lectures, assignments.\n"
        "- delegate_to_browser_agent: Real Chrome browser interaction for web apps.\n"
        "NEVER delegate Canvas LMS tasks to browser_agent."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": TEST_PROMPT},
    ]

    t0 = time.perf_counter()
    resp = await adapter.generate(messages=messages, tools=schemas, temperature=0.0, thinking_enabled=False)
    latency_s = round(time.perf_counter() - t0, 2)

    print(f"Latency: {latency_s}s")
    print(f"Text: {resp.text}")
    print(f"Tool Calls: {json.dumps(resp.tool_calls, indent=2)}")
    
    # Assertions
    calls = resp.tool_calls or []
    tool_names = [c["name"] for c in calls]
    print(f"Emitted tools: {tool_names}")
    
    if "delegate_to_canvas_agent" in tool_names:
        print("[PASS] Successfully selected delegate_to_canvas_agent for Canvas tasks!")
    elif "delegate_to_browser_agent" in tool_names:
        print("[INFO] Model delegated to browser_agent. Checking task content...")
        task_str = calls[0].get("args", {}).get("task", "")
        print(f"Delegated task: {task_str[:200]}...")
    return resp

def probe_observation_classifier():
    print("\n" + "=" * 60)
    print("TEST 2: Observation Classifier (Parked Domain / Quiet Failure Shield)")
    print("=" * 60)

    test_urls = [
        "https://forsale.godaddy.com/notebooklm.app",
        "https://dan.com/buy-domain/notebooklm.com",
        "https://notebooklm.app",
        "https://notebooklm.google.com",
    ]
    for url in test_urls:
        content = "Buy this domain\nThis domain is for sale" if "app" in url else "NotebookLM Welcome"
        surface = classify_page_surface(url, text=content)
        banner = format_surface_banner(surface, url)
        is_parked = (surface == PageSurface.PARKED_DOMAIN)
        print(f"URL: {url:<45} -> Surface: {surface.value:<15} (Parked? {is_parked})")
        if is_parked:
            assert "PARKED DOMAIN" in banner
            print("   -> Correctly generated PARKED DOMAIN shield banner!")

def probe_protocol_normalizer():
    print("\n" + "=" * 60)
    print("TEST 3: Protocol Normalizer (Token Bleed)")
    print("=" * 60)

    # Test the real leak observed in the log trace
    raw_leaked_dict = {"target": "</parameter>\n<parameter=boxes>\ntrue"}
    repaired_dict = LiteLLMAdapter._repair_args(raw_leaked_dict)
    print(f"Input:    {raw_leaked_dict}")
    print(f"Repaired: {repaired_dict}")
    assert repaired_dict.get("boxes") is True

    raw_xml_string = "<parameter=query>wireless networks lectures</parameter>"
    repaired_xml = LiteLLMAdapter._repair_args(raw_xml_string)
    print(f"Input XML:    {raw_xml_string}")
    print(f"Repaired XML: {repaired_xml}")
    assert repaired_xml.get("query") == "wireless networks lectures"
    print("[PASS] Token bleed successfully normalized without crashes!")

async def main():
    probe_observation_classifier()
    probe_protocol_normalizer()
    try:
        await probe_supervisor_routing()
    except Exception as e:
        print(f"[ERROR in supervisor routing probe]: {e}")

if __name__ == "__main__":
    asyncio.run(main())
