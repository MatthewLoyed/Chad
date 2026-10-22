r"""
Benchmark voice-lane latency (category=voice) vs agent lane.

Measures wall-clock from WebSocket send → final ai_response (when Kokoro would start).

Run (Brain server must be restarted after code changes):
    $env:PYTHONPATH="."; .venv/Scripts/python.exe tests/manual/probe_voice_turn.py --benchmark
"""

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

from src.shared.plan_markers import sanitize_response_text

ROOT = Path(__file__).resolve().parents[2]

# Default targets the prod Brain; override with --port to probe a dev Brain
# (e.g. 8788) when the running prod server predates the code under test.
BRAIN_PORT = 8767

BENCHMARK_PROMPTS = [
    ("voice_simple", "what time is it?", "voice"),
    ("voice_chat", "hey chad how are you", "voice"),
    ("voice_escalate", "check my email inbox", "voice"),
    ("agent_simple", "what time is it?", "agent"),
]


def _load_token() -> str:
    env_path = ROOT / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CHAD_SERVER_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("CHAD_SERVER_TOKEN not found in .env")


async def voice_turn_probe(
    prompt: str,
    category: str = "voice",
    *,
    thinking_enabled: bool | None = None,
) -> dict:
    import websockets

    token = _load_token()
    conv_id = f"voice_probe_{int(time.time())}_{category}"
    turn_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    metrics = {
        "prompt": prompt,
        "category": category,
        "sent_at": t0,
        "first_event_s": None,
        "final_response_s": None,
        "tts_start_s": None,
        "tool_starts": [],
        "escalated": False,
        "final": None,
        "spoken": None,
        "error": None,
    }

    headers = {"Origin": "http://localhost:5173"}
    try:
        ws_ctx = websockets.connect(
            f"ws://localhost:{BRAIN_PORT}/ws",
            additional_headers=headers,
            ping_interval=None,
            open_timeout=15,
        )
    except TypeError:
        ws_ctx = websockets.connect(
            f"ws://localhost:{BRAIN_PORT}/ws",
            extra_headers=headers,
            ping_interval=None,
            open_timeout=15,
        )

    async with ws_ctx as ws:
        await ws.send(
            json.dumps(
                {
                    "id": "1",
                    "type": "auth",
                    "payload": {},
                    "auth_token": token,
                    "timestamp": time.time(),
                }
            )
        )
        auth = json.loads(await ws.recv())
        if auth.get("payload", {}).get("status") != "success":
            raise RuntimeError(f"Auth failed: {auth}")

        payload = {
            "text": prompt,
            "initiator": "user",
            "conversation_id": conv_id,
            "category": category,
            "turn_id": turn_id,
        }
        if thinking_enabled is not None:
            payload["thinking_enabled"] = thinking_enabled

        await ws.send(
            json.dumps(
                {
                    "id": "2",
                    "type": "user_input",
                    "payload": payload,
                    "auth_token": token,
                    "timestamp": time.time(),
                }
            )
        )

        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            now = time.perf_counter()
            evt = json.loads(raw)
            etype = evt.get("type")
            ep = evt.get("payload", {})

            if metrics["first_event_s"] is None:
                metrics["first_event_s"] = round(now - t0, 3)

            if etype == "ai_response":
                if ep.get("fragment"):
                    continue
                metrics["final"] = ep.get("text", "")
                metrics["final_response_s"] = round(now - t0, 3)
                metrics["tts_start_s"] = metrics["final_response_s"]
                metrics["spoken"] = sanitize_response_text(metrics["final"] or "")
                break
            elif etype == "ai_thought" and ep.get("state") == "tool_start":
                metrics["tool_starts"].append(ep.get("name"))
                if ep.get("name") == "escalate_to_agent":
                    metrics["escalated"] = True

    if metrics["final_response_s"] is None:
        metrics["error"] = "timeout waiting for final ai_response"
    return metrics


def _print_metrics(m: dict) -> None:
    print(f"\n--- {m['category']} | {m['prompt']!r} ---")
    if m.get("error"):
        print(f"  ERROR: {m['error']}")
        return
    print(f"  first event:     {m['first_event_s']}s")
    print(f"  TTS start (final): {m['tts_start_s']}s  ← Kokoro speaks here")
    print(f"  tools:           {m['tool_starts']}")
    print(f"  spoken preview:  {(m['spoken'] or '')[:120]!r}")


async def run_benchmark() -> int:
    print("=== VOICE LANE BENCHMARK ===")
    print("(TTS start = final non-fragment ai_response received)\n")
    print("Warming server (voice chat)...")
    await voice_turn_probe("warmup", "voice", thinking_enabled=False)
    await asyncio.sleep(1)
    rows = []
    for label, prompt, cat in BENCHMARK_PROMPTS:
        thinking = False if cat == "voice" else None
        m = await voice_turn_probe(prompt, cat, thinking_enabled=thinking)
        m["label"] = label
        rows.append(m)
        _print_metrics(m)
        await asyncio.sleep(2)

    print("\n=== SUMMARY ===")
    for m in rows:
        if m.get("tts_start_s") is not None:
            print(f"  {m['label']:16} TTS start: {m['tts_start_s']}s")
        else:
            print(f"  {m['label']:16} FAILED")
    return 0


async def main() -> int:
    global BRAIN_PORT
    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            BRAIN_PORT = int(arg.split("=", 1)[1])
    print(f"(targeting Brain on port {BRAIN_PORT})")
    if "--benchmark" in sys.argv:
        return await run_benchmark()
    prompts = [a for a in sys.argv[1:] if not a.startswith("-")] or [
        "what time is it?",
        "hey chad",
    ]
    for prompt in prompts:
        await voice_turn_probe(prompt, "voice", thinking_enabled=False)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
