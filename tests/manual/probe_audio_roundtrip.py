r"""
Mic-less verification of the voice-mode audio path: TTS -> PCM -> STT.

`verify_voice_mode.py` needs a human to speak into the microphone, so it can't
run unattended. This probe closes the same loop without hardware by using the
local TTS engine as the "speaker": it synthesizes a known phrase, resamples the
PCM to what Whisper expects, transcribes it, and scores the transcript against
the original text.

That exercises both real engines end to end (Kokoro/Orpheus synthesis +
faster-whisper transcription) and catches the failures that matter most --
engine won't load, weights missing, silent/empty audio, garbled transcript --
leaving only the mic, wake word, and loudspeaker for the interactive check.

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe tests/manual/probe_audio_roundtrip.py

Options:
    --engine kokoro   force a TTS engine (default: whatever tts.engine is)
    --keep            write the synthesized audio to voice_roundtrip.wav
"""

import argparse
import asyncio
import re
import sys
import wave

import numpy as np

# Kokoro-82M emits 24 kHz; faster-whisper expects 16 kHz mono int16.
KOKORO_SAMPLE_RATE = 24000
WHISPER_SAMPLE_RATE = 16000

PHRASES = [
    "turn on the living room lamp",
    "what time is it",
    "check my email inbox",
]


def _section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolate int16 PCM to dst_rate.

    STT.transcribe() takes a sample_rate argument but ignores it (Whisper is
    hardcoded to 16 kHz), so the caller has to land on 16 kHz itself.
    """
    if src_rate == dst_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return pcm
    duration = samples.size / src_rate
    dst_n = int(duration * dst_rate)
    src_x = np.arange(samples.size)
    dst_x = np.linspace(0, samples.size - 1, dst_n)
    return np.interp(dst_x, src_x, samples).astype(np.int16).tobytes()


def _normalize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _score(expected: str, actual: str) -> float:
    """Fraction of expected words present in the transcript."""
    want = _normalize(expected)
    got = set(_normalize(actual))
    if not want:
        return 0.0
    return sum(1 for w in want if w in got) / len(want)


def _write_wav(path: str, pcm: bytes, rate: int) -> None:
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=None, help="force a TTS engine (e.g. kokoro)")
    ap.add_argument("--keep", action="store_true", help="save synthesized audio to wav")
    args = ap.parse_args()

    from src.shared.config_manager import ConfigManager

    engine_name = args.engine or ConfigManager.get("tts.engine", "kokoro")
    _section(f"TTS engine: {engine_name}")

    from src.core.tts.tts_player import _load_engine

    tts = _load_engine(engine_name)
    if not tts.is_available():
        print(f"  [FAIL] TTS engine {engine_name!r} unavailable.")
        return 1
    print(f"  [OK] {type(tts).__name__} available.")

    _section("STT engine: faster-whisper")
    from src.core.stt.stt_factory import get_stt_engine

    stt = get_stt_engine()
    if not stt.is_available():
        print("  [FAIL] STT unavailable. pip install -r requirements-voice.txt")
        return 1
    print(f"  [OK] model={ConfigManager.get('stt.model')} "
          f"device={ConfigManager.get('stt.device')}")

    # Orpheus streams at 24 kHz too, so both supported engines share the rate.
    src_rate = KOKORO_SAMPLE_RATE

    _section("Round trip")
    results: list[tuple[str, str, float]] = []
    for phrase in PHRASES:
        print(f"\n  synthesizing: {phrase!r}")
        pcm = await tts.synthesize(phrase)
        if not pcm:
            print("    [FAIL] synthesis returned no audio.")
            results.append((phrase, "", 0.0))
            continue
        secs = len(pcm) / 2 / src_rate
        print(f"    {len(pcm)} bytes (~{secs:.1f}s @ {src_rate}Hz)")

        pcm16k = _resample(pcm, src_rate, WHISPER_SAMPLE_RATE)
        if args.keep:
            path = f"voice_roundtrip_{_normalize(phrase)[0]}.wav"
            _write_wav(path, pcm16k, WHISPER_SAMPLE_RATE)
            print(f"    saved {path}")

        transcript = await stt.transcribe(pcm16k, sample_rate=WHISPER_SAMPLE_RATE)
        score = _score(phrase, transcript)
        print(f"    transcript: {transcript!r}")
        print(f"    word recall: {score:.0%}")
        results.append((phrase, transcript, score))

    _section("Summary")
    ok = True
    for phrase, transcript, score in results:
        verdict = "OK" if score >= 0.8 else "FAIL"
        if score < 0.8:
            ok = False
        print(f"  [{verdict}] {score:.0%}  {phrase!r} -> {transcript!r}")

    if ok:
        print("\n  Audio path verified end to end (TTS -> PCM -> STT).")
        print("  Remaining: mic capture, wake word, loudspeaker (interactive).")
        return 0
    print("\n  Round trip degraded -- see FAIL rows above.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
