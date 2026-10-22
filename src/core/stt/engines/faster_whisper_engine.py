"""
faster-whisper STT engine — local, offline, free.

Wraps SYSTRAN's faster-whisper (CTranslate2 Whisper) for fully offline
transcription. Defaults to the ``small`` model with ``int8`` quantization, the
standard real-time pick (~200ms latency on a modern CPU, ~2GB RAM).

Degrades gracefully: if the ``faster-whisper`` package is not installed or the
model cannot be loaded, :meth:`is_available` returns False and callers can fall
back or disable voice mode without crashing.
"""

import asyncio

import numpy as np

from src.core.stt.base_stt_engine import BaseSTTEngine
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("FasterWhisperSTT")


class FasterWhisperSTTEngine(BaseSTTEngine):
    """Local-first transcription via faster-whisper (CTranslate2)."""

    def __init__(self):
        self._model = None
        self._initialized = False
        self._load_failed = False
        # Config (read once at construction; cheap and avoids per-call lookups)
        self._model_size = ConfigManager.get("stt.model", "small")
        self._device = ConfigManager.get("stt.device", "cpu")
        self._compute_type = ConfigManager.get("stt.compute_type", "int8")
        self._language = ConfigManager.get("stt.language", "en")
        self._beam_size = int(ConfigManager.get("stt.beam_size", 1))
        # VAD filtering inside whisper trims leading/trailing silence for accuracy.
        self._vad_filter = bool(ConfigManager.get("stt.vad_filter", True))

    def _ensure_initialized(self) -> None:
        """Lazy-load the model. Idempotent; safe to call repeatedly."""
        if self._initialized or self._load_failed:
            return
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            self._initialized = True
            logger.info(
                f"faster-whisper ready (model={self._model_size}, "
                f"device={self._device}, compute_type={self._compute_type})"
            )
        except ImportError:
            self._load_failed = True
            logger.error(
                "faster-whisper not installed. Install with: "
                "pip install -r requirements-voice.txt"
            )
        except Exception as e:
            self._load_failed = True
            logger.error(f"Failed to initialize faster-whisper: {e}")

    def is_available(self) -> bool:
        self._ensure_initialized()
        return self._initialized and self._model is not None

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        if not self.is_available():
            return ""
        if not pcm:
            return ""

        def _run() -> str:
            # 16-bit PCM -> float32 normalized to [-1, 1], as Whisper expects.
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size == 0:
                return ""
            segments, _info = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=self._beam_size,
                vad_filter=self._vad_filter,
            )
            # segments is a lazy generator; materialize and join.
            return "".join(seg.text for seg in segments).strip()

        try:
            # Transcription is CPU-bound and blocking — keep the event loop free.
            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""
