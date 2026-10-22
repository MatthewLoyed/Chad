import asyncio
import logging
import re

import numpy as np
from src.core.tts.base_tts_engine import BaseTTSEngine
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("KokoroTTSEngine")

DEFAULT_VOICE = "am_michael"
# Kokoro voice ids are <lang><gender>_<name>, e.g. am_michael / af_heart.
# Other engines (Orpheus: "leo", Gemini: "Puck") use their own namespaces, and
# passing one of those here 404s the voice download and returns no audio.
_KOKORO_VOICE_RE = re.compile(r"^[a-z][fm]_[a-z]+$")


def _resolve_voice() -> str:
    """Pick a Kokoro-namespaced voice, ignoring other engines' voice names."""
    voice = ConfigManager.get("tts.kokoro_voice") or ConfigManager.get("tts.voice")
    if not voice:
        return DEFAULT_VOICE
    if not _KOKORO_VOICE_RE.match(str(voice)):
        logger.warning(
            f"'{voice}' is not a Kokoro voice id (expected e.g. '{DEFAULT_VOICE}'); "
            f"set tts.kokoro_voice. Falling back to '{DEFAULT_VOICE}'."
        )
        return DEFAULT_VOICE
    return str(voice)


class KokoroTTSEngine(BaseTTSEngine):
    """
    Local-first TTS engine wrapping KPipeline.
    High-fidelity, ultra-low latency, and fully offline.
    """
    def __init__(self):
        self._pipeline = None
        self._voice = _resolve_voice()
        self._initialized = False

    def _ensure_initialized(self):
        """Lazy initialization of KPipeline."""
        if self._initialized:
            return
        try:
            from kokoro import KPipeline
            # lang_code 'a' for American English
            self._pipeline = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
            self._initialized = True
            logger.info(f"Kokoro engine initialized successfully (Voice: {self._voice})")
        except ImportError:
            logger.error("Kokoro library not found. Install with: pip install kokoro")
        except Exception as e:
            logger.error(f"Failed to initialize Kokoro: {e}")

    def is_available(self) -> bool:
        self._ensure_initialized()
        return self._initialized and self._pipeline is not None

    async def synthesize(self, text: str) -> bytes:
        if not self.is_available():
            return b""

        try:
            # Kokoro synthesis is CPU-heavy and blocking, run in thread
            def _run_kokoro():
                # generator returns (graphemes, phonemes, audio)
                generator = self._pipeline(text, voice=self._voice, speed=1.0)
                audio_chunks = []
                for _, _, audio in generator:
                    if audio is not None:
                        audio_chunks.append(audio)
                
                if not audio_chunks:
                    return b""
                
                # Concatenate all chunks and convert float32 to int16 PCM
                full_audio = np.concatenate(audio_chunks)
                return (full_audio * 32767).astype(np.int16).tobytes()

            return await asyncio.to_thread(_run_kokoro)
            
        except Exception as e:
            logger.error(f"Kokoro Synthesis Failed: {e}")
            return b""
