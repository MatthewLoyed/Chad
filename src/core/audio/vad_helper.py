"""
VAD (Voice Activity Detection) Helper
Extracted from session_manager.py for cleaner separation of concerns.
"""

import asyncio
import time
import logging
from typing import Optional, Callable
from src.shared.logger import setup_logger

logger = setup_logger("VADHelper")
logger.setLevel(logging.WARNING)


class VADHelper:
    """
    Voice Activity Detection helper.
    Monitors audio levels and detects silence to trigger turn completion.
    """
    
    def __init__(
        self,
        audio_handler,
        silence_threshold_seconds: float = 1.0,
        vad_threshold: float = 0.01
    ):
        """
        Initialize VAD helper.
        
        Args:
            audio_handler: AudioHandler instance for audio input
            silence_threshold_seconds: Seconds of silence before triggering (default 1.0)
            vad_threshold: Audio level threshold for voice detection (default 0.01)
        """
        self.audio = audio_handler
        self.silence_threshold_seconds = silence_threshold_seconds
        self.vad_threshold = vad_threshold
        self._silence_guard_triggered = False
        self._audio_played_this_turn = False
        
    def reset(self):
        """Reset VAD state for new turn."""
        self._silence_guard_triggered = False
        self._audio_played_this_turn = False
    
    def mark_audio_played(self):
        """Mark that audio was played this turn (prevents premature cutoff)."""
        self._audio_played_this_turn = True
    
    async def create_activity_tracked_generator(
        self,
        is_listening_callback: Callable[[], bool],
        is_connected_callback: Callable[[], bool],
        update_activity_callback: Callable[[], None],
        on_silence_callback: Optional[Callable[[], None]] = None
    ):
        """
        Create an audio generator that tracks voice activity.
        
        Args:
            is_listening_callback: Returns True if session is still listening
            is_connected_callback: Returns True if model connection is ready
            update_activity_callback: Called when voice activity detected
            on_silence_callback: Called when silence threshold exceeded
            
        Yields:
            Audio chunks from input stream
        """
        silence_start_time = None
        engine_stabilized = False
        
        try:
            async for audio_chunk in self.audio.input.read_chunks():
                # Stop if session ended
                if not is_listening_callback():
                    break
                
                # Wait for model connection before starting silence detection
                if not is_connected_callback():
                    yield audio_chunk
                    continue
                
                # Log stabilization once
                if not engine_stabilized:
                    engine_stabilized = True
                    logger.info("VAD: Engine stabilized, starting silence detection")
                    silence_start_time = None
                
                # Check audio level
                audio_level = self.audio.input.get_audio_level(audio_chunk)
                
                if audio_level > self.vad_threshold:
                    # Voice detected
                    update_activity_callback()
                    silence_start_time = None
                else:
                    # Silence detected
                    current_time = time.time()
                    if silence_start_time is None:
                        silence_start_time = current_time
                    else:
                        # Don't trigger if AI is responding
                        if self._audio_played_this_turn:
                            silence_start_time = None
                        else:
                            silence_duration = current_time - silence_start_time
                            if silence_duration >= self.silence_threshold_seconds:
                                logger.info(f"VAD: Silence threshold ({self.silence_threshold_seconds}s) exceeded")
                                self._silence_guard_triggered = True
                                if on_silence_callback:
                                    await on_silence_callback()
                                break
                
                yield audio_chunk
                
        except Exception as e:
            logger.error(f"Error in VAD generator: {e}", exc_info=True)
            raise
        finally:
            # Cleanup: unmute microphone
            try:
                self.audio.input.unmute_input()
                logger.debug("VAD cleanup: microphone unmuted")
            except Exception as e:
                logger.warning(f"Error unmuting microphone: {e}")


class SilenceGuard:
    """
    Local silence guard to bypass network-laggy cloud VAD.
    Monitors silence duration and signals turn_complete when threshold exceeded.
    """
    
    def __init__(
        self,
        silence_guard_seconds: float = 1.5,
        grace_period_seconds: float = 2.0
    ):
        """
        Initialize silence guard.
        
        Args:
            silence_guard_seconds: Seconds of silence before signaling turn_complete
            grace_period_seconds: Initial grace period after session start
        """
        self.silence_guard_seconds = silence_guard_seconds
        self.grace_period_seconds = grace_period_seconds
        self._task: Optional[asyncio.Task] = None
        
    async def monitor(
        self,
        is_listening_callback: Callable[[], bool],
        is_connected_callback: Callable[[], bool],
        is_speaking_callback: Callable[[], bool],
        is_tool_active_callback: Callable[[], bool],
        get_last_voice_time_callback: Callable[[], Optional[float]],
        get_session_start_time_callback: Callable[[], Optional[float]],
        get_last_reasoning_time_callback: Callable[[], Optional[float]],
        audio_played_this_turn_callback: Callable[[], bool],
        silence_guard_triggered_callback: Callable[[], bool],
        update_activity_callback: Callable[[], None],
        send_turn_complete_callback: Callable[[], None],
        on_thinking_callback: Optional[Callable[[], None]] = None
    ):
        """
        Monitor silence and signal turn_complete when appropriate.
        
        Args:
            Various callbacks for state checking and actions
        """
        was_connected = False
        
        try:
            while is_listening_callback():
                await asyncio.sleep(0.1)
                
                # Wait for session to be established
                if not is_connected_callback():
                    was_connected = False
                    continue
                
                # Reset silence clock when connection established
                if not was_connected:
                    was_connected = True
                    update_activity_callback()
                    logger.info("Silence Guard: Engine stabilized")
                    continue
                
                # Grace period check
                session_start_time = get_session_start_time_callback()
                if session_start_time:
                    session_age = time.time() - session_start_time
                    if session_age < self.grace_period_seconds:
                        continue
                
                # Skip if AI is responding or executing tools
                if audio_played_this_turn_callback() or is_speaking_callback() or is_tool_active_callback():
                    continue
                
                last_voice_time = get_last_voice_time_callback()
                if not last_voice_time:
                    continue
                
                elapsed = time.time() - last_voice_time
                
                # Extended timeout if reasoning recently received
                effective_guard = self.silence_guard_seconds
                last_reasoning_time = get_last_reasoning_time_callback()
                if last_reasoning_time:
                    time_since_reasoning = time.time() - last_reasoning_time
                    if time_since_reasoning < 3.0:
                        effective_guard = 5.0
                
                if elapsed >= effective_guard:
                    # Check if already triggered
                    if silence_guard_triggered_callback():
                        continue
                    
                    logger.info(f"Silence guard: {elapsed:.2f}s elapsed (threshold: {effective_guard}s)")
                    
                    # Transition to thinking state
                    if on_thinking_callback:
                        await on_thinking_callback()
                    
                    # Signal turn_complete
                    await send_turn_complete_callback()
                    update_activity_callback()
                    
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in silence guard: {e}", exc_info=True)
    
    def start(self, *args, **kwargs):
        """Start monitoring in background task."""
        if self._task:
            self._task.cancel()
        self._task = asyncio.create_task(self.monitor(*args, **kwargs))
        return self._task
    
    async def stop(self):
        """Stop monitoring."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None









