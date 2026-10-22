import asyncio
import re
import logging
import numpy as np
import sounddevice as sd
from typing import Optional

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.core.tts.base_tts_engine import BaseTTSEngine

logger = setup_logger("TTSPlayer")

class TTSPlayer:
    """
    Engine-agnostic TTS Player.
    Orchestrates synthesis and playback via swappable engines.
    Maintains a dual-queue pipeline for zero-latency sequential audio.
    """
    def __init__(self, engine: BaseTTSEngine):
        self._engine = engine
        self._enabled = ConfigManager.get("tts.enabled", True)
        self._sample_rate = 24000
        
        # Dual Queue Architecture
        self._text_queue = None
        self._audio_queue = None
        self._text_queue_bound = False
        
        self._synthesis_task = None
        self._playback_task = None
        self._is_playing = False
        self._playback_handler = None # Optional custom playback target

    def _start_worker(self):
        """Starts background workers bound to the current event loop."""
        try:
            loop = asyncio.get_running_loop()
            
            if not self._text_queue_bound:
                self._text_queue = asyncio.Queue()
                self._audio_queue = asyncio.Queue()
                self._text_queue_bound = True

            if self._synthesis_task is None or self._synthesis_task.done():
                self._synthesis_task = loop.create_task(self._synthesis_worker())
            if self._playback_task is None or self._playback_task.done():
                self._playback_task = loop.create_task(self._playback_worker())
        except RuntimeError:
            logger.error("Failed to start TTS: No running event loop.")

    async def _synthesis_worker(self):
        """Pipeline 1: Pulls text and synthesizes it via the current engine."""
        while True:
            try:
                if not self._text_queue_bound:
                    await asyncio.sleep(1)
                    continue

                text = await self._text_queue.get()
                if not text or not self._enabled:
                    self._text_queue.task_done()
                    continue

                try:
                    # Generic synthesis call to the engine
                    audio_bytes = await self._engine.synthesize(text)
                    
                    if audio_bytes:
                        await self._audio_queue.put(audio_bytes)
                    else:
                        logger.warning(f"Synthesis failed for text: {text[:50]}...")

                except Exception as e:
                    logger.error(f"Synthesis Error: {e}")
                finally:
                    logger.info(f"TTS: Synthesis finished for: {text[:30]}...")
                    self._text_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Synthesis Worker Error: {e}")
                await asyncio.sleep(1)

    async def _playback_worker(self):
        """Pipeline 2: High-fidelity sequential playback driver."""
        while True:
            try:
                if not self._audio_queue:
                    await asyncio.sleep(1)
                    continue

                audio_bytes = await self._audio_queue.get()
                if not audio_bytes or not self._enabled:
                    self._audio_queue.task_done()
                    continue

                self._is_playing = True
                try:
                    if self._playback_handler:
                        logger.info(f"TTS: Playing via custom handler ({len(audio_bytes)} bytes)")
                        await self._playback_handler(audio_bytes)
                    else:
                        logger.info(f"TTS: Playing via local hardware ({len(audio_bytes)} bytes)")
                        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                        # Use to_thread for blocking sounddevice calls
                        await asyncio.to_thread(sd.play, audio_array, self._sample_rate)
                        await asyncio.to_thread(sd.wait)
                except Exception as e:
                    logger.error(f"Playback Error: {e}")
                finally:
                    self._is_playing = False
                    self._audio_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Playback Worker Error: {e}")
                await asyncio.sleep(0.5)

    async def speak(self, text: str):
        """Synthesizes and speaks text. Call from any async context."""
        if not self._enabled:
            return

        self._start_worker()
        clean_text = self._strip_markdown(text).strip()
        
        if clean_text:
            await self._text_queue.put(clean_text)

    def _strip_markdown(self, text: str) -> str:
        """Cleans formatting while preserving natural flow."""
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'`+', '', text)
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
        return text.strip()

    def set_playback_handler(self, handler):
        """Register a custom playback target (e.g., AudioOutput queue)."""
        self._playback_handler = handler
        logger.info("TTS custom playback handler registered.")

    def set_enabled(self, enabled: bool):
        """Toggle TTS globally."""
        self._enabled = enabled
        if not enabled:
            self.stop()
        logger.info(f"TTS set to {enabled}")

    def stop(self):
        """Emergency stop and queue wipe."""
        sd.stop()
        for q in [self._text_queue, self._audio_queue]:
            if q:
                try:
                    while not q.empty():
                        q.get_nowait()
                        q.task_done()
                except: pass
        logger.info("TTS Stop called and queues cleared.")

# Singleton Factory
_player: Optional[TTSPlayer] = None

class FallbackTTSEngine(BaseTTSEngine):
    """Speaks via ``primary``, falling back to ``fallback`` when it yields nothing.

    Engines report failure by returning empty bytes (LM Studio down, missing
    weights, HTTP error), which otherwise surfaces as Chad silently not
    speaking at all. Wrapping them keeps voice mode audible through a transient
    primary-engine outage. The fallback is constructed lazily so its weights are
    only loaded if the primary actually fails.
    """

    def __init__(self, primary: BaseTTSEngine, fallback_name: str):
        self._primary = primary
        self._fallback_name = fallback_name
        self._fallback: Optional[BaseTTSEngine] = None

    def _get_fallback(self) -> Optional[BaseTTSEngine]:
        if self._fallback is None:
            try:
                self._fallback = _load_engine(self._fallback_name)
            except Exception as e:
                logger.error(f"Fallback TTS engine '{self._fallback_name}' failed to load: {e}")
                return None
        return self._fallback

    def is_available(self) -> bool:
        if self._primary.is_available():
            return True
        fallback = self._get_fallback()
        return bool(fallback and fallback.is_available())

    async def synthesize(self, text: str) -> bytes:
        try:
            audio = await self._primary.synthesize(text)
        except Exception as e:
            logger.error(f"Primary TTS engine raised: {e}")
            audio = b""
        if audio:
            return audio

        fallback = self._get_fallback()
        if not fallback:
            return b""
        logger.warning(
            f"Primary TTS produced no audio; falling back to '{self._fallback_name}'."
        )
        try:
            return await fallback.synthesize(text)
        except Exception as e:
            logger.error(f"Fallback TTS engine '{self._fallback_name}' raised: {e}")
            return b""


def get_tts_player() -> TTSPlayer:
    """Returns/initializes the global TTSPlayer instance."""
    global _player
    if _player is None:
        engine_name = ConfigManager.get("tts.engine", "kokoro")
        engine = _load_engine(engine_name)
        fallback_name = ConfigManager.get("tts.fallback_engine")
        if fallback_name and fallback_name != engine_name:
            engine = FallbackTTSEngine(engine, str(fallback_name))
        _player = TTSPlayer(engine)
    return _player

def _load_engine(name: str) -> BaseTTSEngine:
    """Factory to load specific TTS engines based on config."""
    if name == "gemini":
        from src.core.tts.engines.gemini_engine import GeminiTTSEngine
        return GeminiTTSEngine()
    elif name == "kokoro":
        from src.core.tts.engines.kokoro_engine import KokoroTTSEngine
        return KokoroTTSEngine()
    elif name == "orpheus":
        from src.core.tts.engines.orpheus_engine import OrpheusTTSEngine
        return OrpheusTTSEngine()
    
    logger.warning(f"Unknown TTS engine '{name}', falling back to Kokoro.")
    from src.core.tts.engines.kokoro_engine import KokoroTTSEngine
    return KokoroTTSEngine()
