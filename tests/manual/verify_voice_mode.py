r"""
Manual verification for the discrete "voice mode" pipeline.

Checks the two external dependencies voice mode adds, end to end, WITHOUT booting
the full Brain:

  1. faster-whisper (local STT) loads, and a live mic recording transcribes.
  2. openWakeWord loads the configured wake word model (free, local, no account).

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe tests/manual/verify_voice_mode.py

This is interactive (it records from your microphone), so it is NOT part of the
automated suite.
"""

import asyncio
import sys

from src.shared.config_manager import ConfigManager


def _section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


async def check_stt() -> bool:
    _section("1. faster-whisper STT")
    from src.core.stt.stt_factory import get_stt_engine

    engine = get_stt_engine()
    print(f"  model={ConfigManager.get('stt.model')} "
          f"device={ConfigManager.get('stt.device')} "
          f"compute_type={ConfigManager.get('stt.compute_type')}")
    print("  Loading model (first run downloads weights)...")
    if not engine.is_available():
        print("  [FAIL] STT engine unavailable.")
        print("         Install with: pip install -r requirements-voice.txt")
        return False
    print("  [OK] Model loaded.")

    # Live mic capture via the same AudioInput + UtteranceRecorder used in prod.
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        print("  [SKIP] sounddevice not installed; skipping live mic test.")
        return True

    from src.core.audio.audio_handler import AudioInput
    from src.core.audio.utterance_recorder import UtteranceRecorder

    loop = asyncio.get_running_loop()
    mic = AudioInput(sample_rate=16000, channels=1, loop=loop)
    mic.start()
    rec = UtteranceRecorder(mic, silence_timeout=1.2, max_seconds=10.0, start_timeout=8.0)
    try:
        print("\n  >>> Speak a command now (e.g. 'turn on the lamp')...")
        pcm = await rec.record()
        if not pcm:
            print("  [WARN] No speech captured (start_timeout). Mic working?")
            return True
        print(f"  Captured {len(pcm) / 2 / 16000:.1f}s of audio. Transcribing...")
        text = await engine.transcribe(pcm, sample_rate=16000)
        print(f"  Transcript: {text!r}")
        print("  [OK] Mic -> STT works." if text else "  [WARN] Empty transcript.")
    finally:
        mic.stop()
    return True


def check_wake_word() -> bool:
    from src.core.audio.wake_word import WakeWordDetector

    _section("2. openWakeWord 'Hey Chad' wake word")
    wake_config = ConfigManager.get("wake_word", {})

    try:
        WakeWordDetector(config=wake_config, audio_sample_rate=16000)
        print(f"  [OK] Loaded keyword: {wake_config.get('keyword_path') or wake_config.get('keyword')}")
        return True
    except Exception as e:
        print(f"  [FAIL] Wake word failed to load: {e}")
        print("         Install with: pip install -r requirements-voice.txt")
        return False


async def main() -> int:
    print("Voice Mode verification")
    print(f"  voice_mode.enabled = {ConfigManager.get('voice_mode.enabled')}")
    stt_ok = await check_stt()
    wake_ok = check_wake_word()

    _section("Summary")
    print(f"  STT (faster-whisper): {'OK' if stt_ok else 'FAIL'}")
    print(f"  Wake word: {'OK' if wake_ok else 'FAIL'}")
    if stt_ok and wake_ok:
        print("\n  All good. Set voice_mode.enabled: true in config.yaml and run server.py.")
        return 0
    print("\n  Fix the FAIL items above before enabling voice_mode.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
