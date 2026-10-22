import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Force UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import websockets
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")

def tok(text: str) -> int:
    return len(enc.encode(text)) if text else 0

SERVER_URL = os.getenv("CHAD_SERVER_URL", "ws://localhost:8788/ws")

from scripts.measure_prompt_breakdown import load_auth_token, make_msg
from scripts.detailed_prompt_breakdown import analyze_query

QUERIES = [
    {
        "name": "Drive + Memory + File Write",
        "prompt": "Search my Google Drive for files mentioning wedding budget, look up in memory what we discussed about catering costs, and draft a summary in artifacts/wedding_budget_summary.md",
    },
    {
        "name": "Web Search + Local Code Inspection + Calendar",
        "prompt": "Search the web for Python 3.13 changelog features, search local files in src/core/orchestration for deprecated imports, and check my upcoming calendar for free slots this week.",
    },
    {
        "name": "Gmail + Workspace Docs + Planning",
        "prompt": "Check my recent emails for messages about quarterly project roadmap, search our workspace for architecture docs, and outline the next steps.",
    },
]

async def probe_live_ws(prompt: str) -> dict:
    auth_token = load_auth_token()
    conv_id = f"eval-{uuid.uuid4().hex[:8]}"
    headers = {"Origin": "http://localhost:5173"}
    
    first_token_usage = None
    first_tool_request = None
    tools_menu = None
    turn_mode = None
    
    try:
        ws_ctx = websockets.connect(
            SERVER_URL, additional_headers=headers,
            ping_interval=None, open_timeout=15
        )
    except TypeError:
        ws_ctx = websockets.connect(
            SERVER_URL, extra_headers=headers,
            ping_interval=None, open_timeout=15
        )
        
    async with ws_ctx as ws:
        await ws.send(make_msg("auth", auth_token=auth_token))
        raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
        auth_reply = json.loads(raw)
        if auth_reply.get("type") != "auth" or auth_reply.get("payload", {}).get("status") != "success":
            raise RuntimeError(f"Auth failed: {auth_reply}")
            
        user_msg = make_msg("user_input", {
            "text": prompt,
            "initiator": "user",
            "conversation_id": conv_id,
            "category": "agent",
        })
        await ws.send(user_msg)
        
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                if first_token_usage:
                    break
                continue
                
            evt = json.loads(raw)
            etype = evt.get("type")
            epayload = evt.get("payload", {})
            
            if etype == "turn_mode" and not turn_mode:
                turn_mode = epayload.get("mode")
            elif etype == "token_usage":
                # The first non-zero prompt_tokens is the first model invocation!
                pt = epayload.get("prompt_tokens", 0)
                if pt > 0 and not first_token_usage:
                    first_token_usage = epayload
                    print(f"    [LIVE MODEL USAGE] prompt_tokens={pt}, total={epayload.get('total_tokens')}, reasoning={epayload.get('reasoning_tokens')}")
                    # We have the first turn prompt size! We can break or let it complete
                    break
            elif etype == "tool_request" and not first_tool_request:
                first_tool_request = epayload.get("name")
            elif etype == "ai_thought":
                if epayload.get("state") == "tool_start" and not first_tool_request:
                    first_tool_request = epayload.get("name")
            elif etype == "ai_response" and not epayload.get("fragment", False):
                break
                
    return {
        "live_usage": first_token_usage,
        "first_tool": first_tool_request,
        "turn_mode": turn_mode,
    }

async def run_benchmark():
    results = []
    for item in QUERIES:
        name = item["name"]
        prompt = item["prompt"]
        print(f"\n=======================================================")
        print(f"BENCHMARKING: {name}")
        print(f"Prompt: {prompt}")
        print(f"=======================================================")
        
        # 1. Live WebSocket run against the server
        print("  --> Probing live Brain over WebSocket...")
        live_res = await probe_live_ws(prompt)
        
        # 2. Detailed component breakdown
        print("  --> Analyzing prompt component breakdown...")
        breakdown = await analyze_query(prompt)
        
        combined = {
            "name": name,
            "prompt": prompt,
            "live_measured_prompt_tokens": live_res.get("live_usage", {}).get("prompt_tokens") if live_res.get("live_usage") else None,
            "live_measured_total_tokens": live_res.get("live_usage", {}).get("total_tokens") if live_res.get("live_usage") else None,
            "breakdown": breakdown,
        }
        results.append(combined)
        
    print("\n\n" + "="*80)
    print("FINAL BENCHMARK SUMMARY TABLE:")
    print("="*80)
    out_file = PROJECT_ROOT / "data" / "prompt_reduction_benchmark_results.json"
    out_file.parent.mkdir(exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Results written to {out_file}")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
