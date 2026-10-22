"""
Probe Orpheus TTS via LM Studio (co-load) and write a WAV file.

Usage:
  $env:PYTHONPATH="."; .\\.venv\\Scripts\\python.exe tests/manual/probe_orpheus_tts.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.core.tts.engines.orpheus_engine import OrpheusTTSEngine, SAMPLE_RATE

OUTPUT = project_root / "tests" / "data" / "orpheus_probe.wav"
TEST_TEXT = "Hello, this is Chad using Orpheus text to speech through LM Studio."


def check_lm_studio() -> list[str]:
    url = "http://127.0.0.1:1234/v1/models"
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = json.loads(resp.read())
        return [m["id"] for m in body.get("data", []) if m.get("id")]


def write_wav(pcm: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)


async def main() -> int:
    print("=" * 60)
    print("Orpheus TTS probe (LM Studio co-load)")
    print("=" * 60)

    print("\n[1] LM Studio /v1/models")
    try:
        loaded = check_lm_studio()
        print(f"    Loaded ({len(loaded)}): {', '.join(loaded[:5])}{'...' if len(loaded) > 5 else ''}")
    except Exception as e:
        print(f"    FAIL: {e}")
        return 1

    orpheus_hits = [m for m in loaded if "orpheus" in m.lower()]
    if not orpheus_hits:
        print("    FAIL: no Orpheus model in loaded list — load it in LM Studio first.")
        return 1
    print(f"    Orpheus instance(s): {orpheus_hits}")

    print("\n[2] OrpheusTTSEngine availability")
    engine = OrpheusTTSEngine()
    if not engine.is_available():
        print(f"    FAIL: {engine.unavailable_reason}")
        return 1
    print(f"    OK — resolved model: {engine._resolved_model_id}, voice: {engine._voice}")

    print(f"\n[3] Synthesize (voice={engine._voice})")
    print(f"    Text: {TEST_TEXT!r}")
    t0 = time.perf_counter()
    pcm = await engine.synthesize(TEST_TEXT)
    elapsed = time.perf_counter() - t0

    if not pcm or len(pcm) < 1000:
        print(f"    FAIL: empty or tiny PCM ({len(pcm)} bytes) in {elapsed:.1f}s")
        print(f"    Reason: {engine.unavailable_reason}")
        return 1

    duration_s = len(pcm) / (2 * SAMPLE_RATE)
    print(f"    OK — {len(pcm)} bytes PCM, ~{duration_s:.2f}s audio, {elapsed:.1f}s wall")

    print(f"\n[4] Write WAV -> {OUTPUT}")
    write_wav(pcm, OUTPUT)
    print(f"    OK — {OUTPUT.stat().st_size} bytes on disk")
    print("\nDone. Play the WAV to confirm Leo voice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
