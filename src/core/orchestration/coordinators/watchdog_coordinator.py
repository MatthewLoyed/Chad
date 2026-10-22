"""
WatchdogCoordinator

Handles background monitoring of neural hangs, zombie turns, and hardware interrupts.
Extracted from SessionManager to keep orchestration loops decoupled from physics loops.
"""

import asyncio
import time
import logging
from typing import Callable, Optional

from src.shared.debug_helper import debug_print
from src.shared.logger import setup_logger

logger = setup_logger("WatchdogCoordinator")

# Timeouts
ZOMBIE_TURN_TIMEOUT = 5.0
NEURAL_HANG_TIMEOUT = 12.0

class WatchdogCoordinator:
    """
    Monitors session background states, including timeouts and wake word interrupts.
    """
    def __init__(
        self,
        wake_word_detector=None,
        on_interrupt_detected: Optional[Callable] = None,
        on_zombie_turn_recovered: Optional[Callable] = None,
        on_neural_hang_recovered: Optional[Callable] = None
    ):
        self.wake_word_detector = wake_word_detector
        
        # Callbacks
        self.on_interrupt_detected = on_interrupt_detected
        self.on_zombie_turn_recovered = on_zombie_turn_recovered
        self.on_neural_hang_recovered = on_neural_hang_recovered

    async def monitor_interrupt(self, is_listening_fn: Callable[[], bool]) -> None:
        """
        Background task watching the hardware wake word detector for physical interrupts.
        """
        if not self.wake_word_detector:
            return
            
        try:
            while is_listening_fn():
                await asyncio.sleep(0.1)
                if getattr(self.wake_word_detector, 'is_keyword_detected', False):
                    self.wake_word_detector.is_keyword_detected = False  # Reset flag
                    
                    if self.on_interrupt_detected:
                        await self.on_interrupt_detected()
                        return  # Exit after triggering
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in Watchdog interrupt monitor: {e}", exc_info=True)

    async def monitor_timeout(
        self,
        is_listening_fn: Callable[[], bool],
        get_last_ai_activity_time: Callable[[], float],
        is_audio_played_fn: Callable[[], bool],
        is_audio_playing_fn: Callable[[], bool],
        is_tool_active_fn: Callable[[], bool],
        get_current_state: Callable[[], str],
        get_diagnostic_info: Callable[[], str]
    ) -> None:
        """
        Background task that monitors for timeout and neural hangs.
        Only counts silence AFTER AI finishes speaking.
        """
        try:
            while is_listening_fn():
                await asyncio.sleep(1.0)
                
                # ZOMBIE TURN CHECK
                if is_audio_played_fn():
                    if is_audio_playing_fn():
                        continue
                        
                    elapsed_post_audio = time.time() - get_last_ai_activity_time()
                    if elapsed_post_audio > ZOMBIE_TURN_TIMEOUT:
                        logger.debug(f"Zombie Turn detected ({elapsed_post_audio:.1f}s silence)")
                        logger.debug(f"Zombie Diagnostic: {get_diagnostic_info()}")
                        logger.warning("[ SYSTEM ] : Zombie Turn Detected. Forcing wrap-up.")
                        
                        if self.on_zombie_turn_recovered:
                            await self.on_zombie_turn_recovered()
                        return

                # INTELLIGENT WATCHDOG: Detect neural hangs during thinking
                if get_current_state() == "thinking":
                    elapsed_since_activity = time.time() - get_last_ai_activity_time()
                    if elapsed_since_activity > NEURAL_HANG_TIMEOUT and not is_tool_active_fn():
                        debug_print(f"DEBUG: Neural hang detected after {elapsed_since_activity:.1f}s")
                        logger.warning("[ SYSTEM ] : Neural Hang Detected. Resetting.")
                        
                        if self.on_neural_hang_recovered:
                            await self.on_neural_hang_recovered()
                        return

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in Watchdog timeout monitor: {e}", exc_info=True)
