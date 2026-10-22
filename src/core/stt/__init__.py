"""
Speech-to-Text (STT) subsystem.

Mirrors the structure of ``src/core/tts``: an engine-agnostic interface
(:class:`BaseSTTEngine`) with swappable concrete engines, selected via a
config-driven factory (:func:`get_stt_engine`).
"""

from src.core.stt.base_stt_engine import BaseSTTEngine
from src.core.stt.stt_factory import get_stt_engine

__all__ = ["BaseSTTEngine", "get_stt_engine"]
