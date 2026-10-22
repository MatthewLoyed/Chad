"""
Base STT Engine — engine-agnostic speech-to-text contract.

Concrete engines (e.g. faster-whisper, a cloud API) implement this interface so
the rest of the system can swap transcription backends without code changes.
Mirrors :class:`src.core.tts.base_tts_engine.BaseTTSEngine`.
"""

from abc import ABC, abstractmethod


class BaseSTTEngine(ABC):
    """Abstract transcription engine. Input is raw PCM, output is text."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the engine is installed/loaded and ready to transcribe."""
        raise NotImplementedError

    @abstractmethod
    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        """
        Transcribe a single utterance.

        Args:
            pcm: Raw 16-bit signed mono PCM audio bytes.
            sample_rate: Sample rate of ``pcm`` in Hz (default 16000).

        Returns:
            The transcribed text (stripped). Empty string on failure or silence.
        """
        raise NotImplementedError
