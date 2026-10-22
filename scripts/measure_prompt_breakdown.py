import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Configure utf-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import websockets

SERVER_URL = os.getenv("CHAD_SERVER_URL", "ws://localhost:8788/ws")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def load_auth_token() -> str:
    # 1. Environment
    for k in ("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN"):
        if os.getenv(k):
            return os.environ[k].strip()
    # 2. .env file
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k in ("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN") and v:
                    return v
    raise RuntimeError("Could not find auth token")

def make_msg(msg_type: str, payload: dict = None, auth_token: str = None) -> str:
    return json.dumps({
        "id": str(uuid.uuid4())[:8],
        "type": msg_type,
        "payload": payload or {},
        "auth_token": auth_token,
        "timestamp": time.time(),
    })

async def run_probe(prompt: str, category: str = "agent"):
    auth_token = load_auth_token()
    conv_id = f"probe-{uuid.uuid4().hex[:8]}"
    print(f"\n=======================================================")
    print(f"Connecting to {SERVER_URL} for query:")
    print(f"Prompt: {prompt}")
    print(f"Conversation ID: {conv_id}")
    print(f"Category: {category}")
    print(f"=======================================================\n")

    events = []
    token_usage_events = []
    tool_menus = []
    first_token_time = None
    t0 = time.time()

    headers = {"Origin": "http://localhost:5173"}
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
        # Auth handshake
        await ws.send(make_msg("auth", auth_token=auth_token))
        raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
        auth_reply = json.loads(raw)
        if auth_reply.get("type") != "auth" or auth_reply.get("payload", {}).get("status") != "success":
            raise RuntimeError(f"Auth failed: {auth_reply}")
        print("  -> Auth succeeded")

        # Send user_input
        user_msg = make_msg("user_input", {
            "text": prompt,
            "initiator": "user",
            "conversation_id": conv_id,
            "category": category,
        })
        await ws.send(user_msg)
        print("  -> Sent user_input. Waiting for stream events...")

        # Collect stream
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                if any(e.get("type") == "ai_response" and not e.get("payload", {}).get("fragment", False) for e in events):
                    print("  -> Stream concluded after final ai_response.")
                    break
                continue

            evt = json.loads(raw)
            etype = evt.get("type")
            epayload = evt.get("payload", {})
            events.append(evt)

            if etype == "token_usage":
                token_usage_events.append(epayload)
                print(f"  [TOKEN_USAGE] {epayload}")
            elif etype == "turn_mode":
                print(f"  [TURN_MODE] {epayload}")
            elif etype == "tool_request":
                print(f"  [TOOL_REQUEST] {epayload.get('name')}")
            elif etype == "ai_thought":
                state = epayload.get("state")
                if state in ("tool_start", "tool_end"):
                    print(f"  [THOUGHT:{state}] {epayload.get('name')}")
            elif etype == "ai_response":
                is_frag = epayload.get("fragment", False)
                if first_token_time is None and is_frag:
                    first_token_time = time.time() - t0
                    print(f"  [FIRST TOKEN] at {first_token_time:.2f}s")
                if not is_frag:
                    print(f"  [AI_RESPONSE FINAL] ({len(epayload.get('text', ''))} chars)")
                    break

    elapsed = time.time() - t0
    print(f"\nCompleted in {elapsed:.2f}s. Captured {len(events)} events.")
    return {
        "prompt": prompt,
        "elapsed": elapsed,
        "token_usage_events": token_usage_events,
        "all_events": events,
    }

if __name__ == "__main__":
    test_prompt = sys.argv[1] if len(sys.argv) > 1 else "What time is it right now?"
    asyncio.run(run_probe(test_prompt))
