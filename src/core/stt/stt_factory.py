"""
STT engine factory.

Selects and constructs the configured speech-to-text engine. Mirrors the
``_load_engine`` factory in :mod:`src.core.tts.tts_player`.
"""

from typing import Optional

from src.core.stt.base_stt_engine import BaseSTTEngine
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("STTFactory")

_engine: Optional[BaseSTTEngine] = None


def _build_engine(name: str) -> BaseSTTEngine:
    """Construct a fresh engine instance for the given name."""
    if name == "faster_whisper":
        from src.core.stt.engines.faster_whisper_engine import FasterWhisperSTTEngine

        return FasterWhisperSTTEngine()

    logger.warning(f"Unknown STT engine '{name}', falling back to faster_whisper.")
    from src.core.stt.engines.faster_whisper_engine import FasterWhisperSTTEngine

    return FasterWhisperSTTEngine()


def get_stt_engine() -> BaseSTTEngine:
    """Return the process-wide STT engine singleton (lazy-constructed)."""
    global _engine
    if _engine is None:
        engine_name = ConfigManager.get("stt.engine", "faster_whisper")
        _engine = _build_engine(engine_name)
    return _engine
