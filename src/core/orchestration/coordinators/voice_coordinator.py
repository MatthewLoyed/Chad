"""
VoiceCoordinator

Handles physical hardware connections (PyAudio generators) and persistent WebSocket streams.
Extracted from SessionManager.
"""

import asyncio
import time
import logging
from typing import AsyncGenerator, Callable, Dict, Any, Optional

from src.shared.logger import setup_logger
from src.shared.debug_helper import debug_print

logger = setup_logger("VoiceCoordinator")
logger.setLevel(logging.WARNING)

# Extracted constraints
VAD_THROUGH_THRESHOLD = 0.01

class VoiceCoordinator:
    """
    Coordinates hardware audio streaming to and from the AI model via Websockets.
    """
    def __init__(
        self,
        audio_handler,
        active_model_fn: Callable[[], Any],
        on_audio_level_fn: Callable[[float], Any] = None,
        on_activity_time_updated_fn: Callable[[], None] = None
    ):
        self.audio = audio_handler
        self.get_active_model = active_model_fn
        self.on_audio_level = on_audio_level_fn
        self.on_activity_time_updated = on_activity_time_updated_fn

    async def create_activity_tracked_audio_generator(
        self,
        is_listening_fn: Callable[[], bool],
        session_start_time: float,
        is_audio_played_fn: Callable[[], bool]
    ) -> AsyncGenerator[bytes, None]:
        """
        Create an audio generator that pushes audio chunks from the microphone.
        """
        logger.info("[ AUDIO GEN ] : Audio generator CREATED via VoiceCoordinator")
        chunks_yielded = 0
        
        try:
            async for audio_chunk in self.audio.input.read_chunks():
                chunks_yielded += 1
                
                # Stop generating if session was stopped
                if not is_listening_fn():
                    logger.info(f"[ AUDIO GEN ] : Session stopped, ending generator")
                    break
                
                active_model = self.get_active_model()
                model_connected = (
                    active_model is not None and 
                    hasattr(active_model, 'is_connected') and 
                    active_model.is_connected
                )
                
                if not model_connected:
                    yield audio_chunk
                    continue
                
                # Check audio level to detect voice activity
                audio_level = self.audio.input.get_audio_level(audio_chunk)
                
                # PUSH TO UI: High-frequency reactivity during conversation
                if self.on_audio_level:
                    await self.on_audio_level(audio_level)
                
                if audio_level > VAD_THROUGH_THRESHOLD:
                    if self.on_activity_time_updated:
                        self.on_activity_time_updated()
                
                yield audio_chunk
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in audio generator: {e}", exc_info=True)
            raise
        finally:
            # GRACEFUL CLEANUP: Ensure mic is unmuted if generator exits abnormally
            try:
                if hasattr(self.audio, 'input') and self.audio.input:
                    self.audio.input.unmute_input()
            except Exception:
                pass

    async def execute_interrupt_protocol(self) -> None:
        """
        Execute the hardware-level part of the interruption protocol.
        - Stops current playback
        - Clears output and input queues
        - Restarts the stream for acknowledgment sounds
        """
        logger.warning("[ AUDIO ] : Executing Hardware Interrupt Protocol.")
        
        # 1. Stop Audio immediately
        if hasattr(self.audio, 'output') and self.audio.output:
            try:
                self.audio.output.stop() # Hard stop stream
            except Exception as e:
                logger.warning(f"Failed to stop audio output in interrupt protocol: {e}")
                
            try:
                 # Restart stream immediately so we can play the acknowledgment sound
                self.audio.output.start()
            except Exception as e:
                logger.warning(f"Failed to restart audio output after interrupt: {e}")
                
            if hasattr(self.audio.output, 'clear_queue'):
                try:
                    self.audio.output.clear_queue()
                except Exception as e:
                    logger.warning(f"Failed to clear audio output queue: {e}")
        
        # 2. Flush Input Buffers
        # Clear recent audio input to ensure we listen fresh
        if hasattr(self.audio, 'input') and self.audio.input:
            if hasattr(self.audio.input, 'clear_queue'):
                try:
                    self.audio.input.clear_queue()
                except Exception as e:
                    logger.warning(f"Failed to clear audio input queue: {e}")
        
        logger.info("[ AUDIO ] : Hardware Interrupt Protocol Complete.")
