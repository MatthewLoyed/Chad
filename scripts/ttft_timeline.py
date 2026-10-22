"""Quick TTFT timeline probe — dumps every WS event with its +ms offset from submit.

Reveals where pre-LM-Studio time goes (intent classify, tool selection, supervisor
prefill) vs. actual generation, which the summary benchmark collapses.

    $env:PYTHONPATH="."; .venv\\Scripts\\python.exe scripts\\ttft_timeline.py --prompt "hi"
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_pipeline import load_auth_token, check_server_health, _make_msg, SERVER_URL  # noqa: E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


CATEGORY = "auto"


async def run(prompt: str):
    import websockets
    token = load_auth_token()
    if not check_server_health():
        print("Server not healthy on :8767"); return
    headers = {"Origin": "http://localhost:5173"}
    try:
        ctx = websockets.connect(SERVER_URL, additional_headers=headers, ping_interval=None, open_timeout=20)
    except TypeError:
        ctx = websockets.connect(SERVER_URL, extra_headers=headers, ping_interval=None, open_timeout=20)
    async with ctx as ws:
        await ws.send(_make_msg("auth", auth_token=token))
        await asyncio.wait_for(ws.recv(), timeout=30)
        conv = str(uuid.uuid4())
        t0 = time.time()
        await ws.send(_make_msg("user_input", {
            "text": prompt, "initiator": "user", "conversation_id": conv, "category": CATEGORY,
        }))
        print(f"\n=== timeline for prompt={prompt!r} ===")
        first_text = None
        deadline = t0 + 180
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
            except asyncio.TimeoutError:
                print("  (timeout)"); break
            now = time.time()
            try:
                evt = json.loads(raw)
            except Exception:
                continue
            etype = evt.get("type", "")
            p = evt.get("payload", {}) or {}
            state = p.get("state", "")
            frag = p.get("fragment")
            text = p.get("text") or p.get("thinking_text") or ""
            tlen = len(text) if isinstance(text, str) else 0
            off = (now - t0) * 1000
            # Mark the first event that carries actual model output text
            tag = ""
            if etype == "ai_response" and frag and first_text is None:
                first_text = off; tag = "  <-- FIRST VISIBLE TOKEN"
            extra = ""
            if state == "status":
                extra = f"  STATUS={text!r}"
            if etype == "token_usage":
                extra = f"  prompt_tokens={p.get('prompt_tokens')} completion={p.get('completion_tokens')} ctx={p.get('context_window')} est_prompt={p.get('estimated_prompt_tokens')}"
            print(f"  +{off:8.0f}ms  {etype:<16} state={state:<14} frag={str(frag):<5} textlen={tlen}{tag}{extra}")
            if etype == "ai_response" and not frag:
                print(f"  +{off:8.0f}ms  FINAL ({tlen} chars)")
                break
            if etype in ("error", "system_error"):
                print(f"  ERROR: {p}"); break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="hi")
    ap.add_argument("--category", default="auto")
    a = ap.parse_args()
    CATEGORY = a.category
    asyncio.run(run(a.prompt))
