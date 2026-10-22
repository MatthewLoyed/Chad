"""
Utterance Recorder — capture a single spoken command, end-pointed by silence.

Used by the discrete "voice mode" pipeline after the wake word fires: it records
microphone PCM until the speaker stops talking (energy-based VAD), then returns
the raw audio for transcription.

Deliberately decoupled from the cloud/Gemini-Live VAD machinery in
``vad_helper.py`` (which is entangled with model-connection state). This depends
only on a minimal audio-source protocol so it is trivial to unit-test with a fake.
"""

import asyncio
import time
from typing import AsyncGenerator, Callable, Protocol

from src.shared.logger import setup_logger

logger = setup_logger("UtteranceRecorder")


class AudioSource(Protocol):
    """Minimal interface the recorder needs from an audio input."""

    def read_chunks(self) -> AsyncGenerator[bytes, None]: ...

    def get_audio_level(self, audio_data: bytes) -> float: ...


class UtteranceRecorder:
    """Records one utterance from an :class:`AudioSource`, ended by silence."""

    def __init__(
        self,
        audio_source: AudioSource,
        silence_timeout: float = 1.2,
        max_seconds: float = 15.0,
        vad_threshold: float = 0.01,
        start_timeout: float = 6.0,
        lead_in_seconds: float = 0.0,
        time_source: Callable[[], float] = time.monotonic,
    ):
        """
        Args:
            audio_source: Object exposing ``read_chunks()`` and ``get_audio_level()``.
            silence_timeout: Trailing silence (s) that marks the end of speech.
            max_seconds: Hard cap on utterance length (s).
            vad_threshold: RMS level above which a chunk counts as speech.
            start_timeout: If no speech begins within this many seconds, give up.
                Measured from the end of ``lead_in_seconds``, not from the call.
            lead_in_seconds: Audio to discard before listening for speech at all.
                The activation earcon is played on a background thread and is
                therefore still sounding once recording begins; at the default
                ``vad_threshold`` it easily reads as speech, so the recorder
                would end-point on the beep itself and return a clip containing
                no command (#654). Set to roughly the earcon's length.
            time_source: Monotonic clock, injectable so the timing rules above
                can be tested deterministically instead of with real sleeps.
        """
        self._audio = audio_source
        self._silence_timeout = silence_timeout
        self._max_seconds = max_seconds
        self._vad_threshold = vad_threshold
        self._start_timeout = start_timeout
        self._lead_in_seconds = lead_in_seconds
        self._now = time_source

    async def record(self) -> bytes:
        """
        Block until the speaker finishes (or limits hit), returning raw PCM bytes.

        Returns empty bytes if no speech was detected within ``start_timeout``.
        """
        frames: list[bytes] = []
        started = False
        start_time = self._now()
        # Speech isn't looked for until the lead-in guard expires, so the
        # start_timeout budget begins there rather than at the call.
        listen_from = start_time + self._lead_in_seconds
        last_voice_time = listen_from

        try:
            async for chunk in self._audio.read_chunks():
                now = self._now()

                if now < listen_from:
                    # Still inside the earcon; drop the audio entirely so it can
                    # neither trigger the VAD nor land in the transcript.
                    continue

                level = self._audio.get_audio_level(chunk)
                is_voice = level > self._vad_threshold

                if not started:
                    if is_voice:
                        started = True
                        last_voice_time = now
                        frames.append(chunk)
                        logger.debug("Utterance start detected.")
                    elif (now - listen_from) > self._start_timeout:
                        logger.debug("No speech within start_timeout; aborting capture.")
                        return b""
                    # else: still waiting for the speaker to begin
                    continue

                # Speech in progress: always buffer, then evaluate end-pointing.
                frames.append(chunk)
                if is_voice:
                    last_voice_time = now
                elif (now - last_voice_time) >= self._silence_timeout:
                    logger.debug(
                        f"Utterance end: {self._silence_timeout:.1f}s trailing silence."
                    )
                    break

                if (now - listen_from) >= self._max_seconds:
                    logger.debug("Max utterance length reached; stopping capture.")
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error while recording utterance: {e}", exc_info=True)

        return b"".join(frames)
