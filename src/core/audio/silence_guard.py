"""
Silence Guard - Voice Activity Detection and Silence Monitoring

Extracted from SessionManager to provide focused silence/VAD detection.
Handles:
- Voice Activity Detection (VAD) in audio stream
- Silence duration tracking
- Automatic turn completion signaling
"""

import asyncio
import logging
import time
from typing import Optional, Callable, AsyncGenerator, Any
from dataclasses import dataclass

logger = logging.getLogger("SilenceGuard")

from src.shared.debug_helper import debug_print


# ==========================================
# TIMEOUT & THRESHOLD CONFIGURATION
# ==========================================
@dataclass
class SilenceConfig:
    """Configuration for silence detection."""
    silence_guard_default: float = 1.5       # Seconds of silence before user input is considered finished
    silence_guard_extended: float = 5.0      # Extended silence guard if reasoning was received recently
    reasoning_relevancy_window: float = 6.0  # How recently reasoning must have been received
    handshake_grace_period: float = 2.0      # Initial silence period to ignore during WebSocket handshake
    empty_session_watchdog: float = 6.0      # Wait for model response before closing 'empty' session
    vad_silence_timeout: float = 1.8         # Seconds of silence in Audio Generator before cutoff
    vad_threshold: float = 0.01              # Volume threshold for voice activity detection


class SilenceGuard:
    """
    Monitors audio streams for voice activity and silence.
    
    Provides:
    - Configurable silence thresholds
    - Reasoning-aware timeout extension
    - Handshake grace period handling
    - Activity time tracking
    """
    
    def __init__(
        self,
        config: Optional[SilenceConfig] = None,
        on_silence_detected: Optional[Callable] = None,
        on_voice_activity: Optional[Callable] = None
    ):
        """
        Initialize the silence guard.
        
        Args:
            config: Silence detection configuration
            on_silence_detected: Async callback when silence threshold exceeded
            on_voice_activity: Async callback when voice activity detected
        """
        self.config = config or SilenceConfig()
        self._on_silence_detected = on_silence_detected
        self._on_voice_activity = on_voice_activity
        
        # Tracking state
        self._last_voice_time: Optional[float] = None
        self._last_reasoning_time: Optional[float] = None
        self._session_start_time: Optional[float] = None
        self._silence_triggered: bool = False
        self._is_active: bool = False
        self._engine_stabilized: bool = False
        
    def start_session(self) -> None:
        """Start a new listening session."""
        self._session_start_time = time.time()
        self._last_voice_time = time.time()
        self._silence_triggered = False
        self._is_active = True
        self._engine_stabilized = False
        
    def stop_session(self) -> None:
        """Stop the current session."""
        self._is_active = False
        
    def update_voice_activity(self) -> None:
        """Record voice activity (user speaking)."""
        self._last_voice_time = time.time()
        self._silence_triggered = False
        
    def update_reasoning_activity(self) -> None:
        """Record reasoning activity (AI thinking)."""
        self._last_reasoning_time = time.time()
        
    def reset(self) -> None:
        """Reset all tracking state."""
        self._last_voice_time = None
        self._last_reasoning_time = None
        self._session_start_time = None
        self._silence_triggered = False
        self._is_active = False
        self._engine_stabilized = False
    
    @property
    def is_in_grace_period(self) -> bool:
        """Check if still in handshake grace period."""
        if self._session_start_time is None:
            return True
        return (time.time() - self._session_start_time) < self.config.handshake_grace_period
    
    @property
    def effective_silence_threshold(self) -> float:
        """Get the current effective silence threshold (may be extended for reasoning)."""
        if self._last_reasoning_time is not None:
            time_since_reasoning = time.time() - self._last_reasoning_time
            if time_since_reasoning < self.config.reasoning_relevancy_window:
                return self.config.silence_guard_extended
        return self.config.silence_guard_default
    
    @property
    def silence_duration(self) -> float:
        """Get current silence duration in seconds."""
        if self._last_voice_time is None:
            return 0.0
        return time.time() - self._last_voice_time
    
    def check_silence_threshold(self) -> bool:
        """
        Check if silence threshold has been exceeded.
        
        Returns:
            True if silence threshold exceeded and not already triggered
        """
        if self._silence_triggered:
            return False
        if self._last_voice_time is None:
            return False
        if self.is_in_grace_period:
            return False
            
        if self.silence_duration >= self.effective_silence_threshold:
            self._silence_triggered = True
            return True
        return False
    
    def check_audio_level(self, audio_level: float) -> bool:
        """
        Check audio level for voice activity.
        
        Args:
            audio_level: Audio level from microphone (0.0 to 1.0)
            
        Returns:
            True if voice activity detected
        """
        if audio_level > self.config.vad_threshold:
            self.update_voice_activity()
            return True
        return False
    
    async def monitor_loop(
        self,
        is_listening_fn: Callable[[], bool],
        is_model_connected_fn: Callable[[], bool],
        is_speaking_fn: Callable[[], bool],
        is_tool_active_fn: Callable[[], bool],
        audio_played_fn: Callable[[], bool]
    ) -> None:
        """
        Main monitoring loop for silence detection.
        
        Args:
            is_listening_fn: Function returning True if session is listening
            is_model_connected_fn: Function returning True if model is connected
            is_speaking_fn: Function returning True if AI is speaking
            is_tool_active_fn: Function returning True if tool is executing
            audio_played_fn: Function returning True if audio was played this turn
        """
        debug_print(f"DEBUG: Silence guard started (threshold: {self.config.silence_guard_default}s)")
        
        was_connected = False
        
        try:
            while is_listening_fn():
                await asyncio.sleep(0.1)
                
                # Check if model is connected
                if not is_model_connected_fn():
                    was_connected = False
                    continue
                
                # Reset on connection establishment
                if not was_connected:
                    was_connected = True
                    self._last_voice_time = time.time()
                    self._silence_triggered = False
                    self._engine_stabilized = True
                    logger.info("Silence Guard: Engine stabilized")
                    continue
                
                # Grace period check
                if self.is_in_grace_period:
                    self._last_voice_time = time.time()
                    continue
                
                # Skip if AI is responding or executing tools
                if audio_played_fn() or is_speaking_fn():
                    continue
                if is_tool_active_fn():
                    continue
                
                # Check silence threshold
                if self.check_silence_threshold():
                    debug_print(f"DEBUG: Silence guard triggered: {self.silence_duration:.2f}s silence")
                    
                    if self._on_silence_detected:
                        await self._on_silence_detected()
                    
                    debug_print("DEBUG: Silence detected. Waiting for model response...")
                    return
                    
        except asyncio.CancelledError:
            raise
        except Exception as e:
            debug_print(f"DEBUG: Silence guard exception: {e}")
            logger.error(f"Error in silence guard: {e}", exc_info=True)
