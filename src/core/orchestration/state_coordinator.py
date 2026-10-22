"""
State Coordinator - Centralized State Machine for AI Assistant

Single source of truth for application state transitions.
Extracted from SessionManager to provide a clean, single-responsibility state management.
"""

import logging
import time
from typing import Optional, Callable, Set, Any, Dict
from enum import Enum

logger = logging.getLogger("StateCoordinator")


class AppState(Enum):
    """Valid application states."""
    IDLE = "idle"
    READY = "ready"           # Wake word armed
    LISTENING = "listening"   # User speaking
    THINKING = "thinking"     # AI processing
    SPEAKING = "speaking"     # AI speaking
    ACTIVE = "active"         # Session active (between turns)
    EXECUTING = "executing"   # Tool executing
    MODEL_LOADING = "model_loading" # Loading model into VRAM
    ERROR = "error"


class StateCoordinator:
    """
    Centralized state machine for the AI assistant.
    
    All state transitions go through this coordinator, providing:
    - Single source of truth for current state
    - Consistent state change notifications
    - State transition logging for debugging
    - Prevention of invalid state transitions
    """
    
    # Valid state transitions (from_state -> allowed_to_states)
    VALID_TRANSITIONS = {
        AppState.IDLE: {AppState.READY, AppState.ERROR},
        AppState.READY: {AppState.LISTENING, AppState.ACTIVE, AppState.ERROR, AppState.IDLE},
        AppState.LISTENING: {AppState.THINKING, AppState.ACTIVE, AppState.READY, AppState.ERROR, AppState.MODEL_LOADING},
        AppState.THINKING: {AppState.SPEAKING, AppState.EXECUTING, AppState.ACTIVE, AppState.READY, AppState.ERROR, AppState.THINKING, AppState.MODEL_LOADING},
        AppState.SPEAKING: {AppState.ACTIVE, AppState.LISTENING, AppState.READY, AppState.THINKING, AppState.ERROR},
        AppState.ACTIVE: {AppState.LISTENING, AppState.THINKING, AppState.READY, AppState.ERROR},
        AppState.EXECUTING: {AppState.THINKING, AppState.SPEAKING, AppState.ACTIVE, AppState.READY, AppState.ERROR},
        AppState.MODEL_LOADING: {AppState.THINKING, AppState.ERROR},
        AppState.ERROR: {AppState.READY, AppState.IDLE},
    }
    
    def __init__(self, on_state_change: Optional[Callable] = None):
        """
        Initialize the state coordinator.
        
        Args:
            on_state_change: Async callback fired on state transitions.
                            Signature: async (new_state: str) -> None
        """
        self._current_state: AppState = AppState.IDLE
        self._previous_state: AppState = AppState.IDLE
        self._state_history: list = []  # Recent state transitions for debugging
        self._on_state_change: Optional[Callable] = on_state_change
        
        # Session-level flags (extracted from SessionManager)
        self._is_speaking: bool = False
        self._is_listening: bool = False
        self._is_turn_complete: bool = False
        self._expecting_followup: bool = False
        self._is_tool_active: bool = False
        
        # Activity tracking
        self._last_activity_time: float = time.time()
        self._state_entered_time: float = time.time()
        
        # Turn-level tracking (moved from SessionManager)
        self._turn_active: bool = False
        self._pending_clarification: bool = False
        self._agentic_veto: bool = False
        self._last_reasoning_time: Optional[float] = None
        self._current_user_transcript: Optional[str] = None
        self._interrupt_active: bool = False
        
        logger.debug(f"[ STATE ] : StateCoordinator initialized.")
    
    @property
    def current_state(self) -> str:
        """Get current state as string (for compatibility)."""
        return self._current_state.value
    
    @property
    def current_state_enum(self) -> AppState:
        """Get current state as enum."""
        return self._current_state
    
    @property
    def previous_state(self) -> str:
        """Get previous state as string."""
        return self._previous_state.value
    
    @property
    def is_speaking(self) -> bool:
        """Return True if AI is currently speaking."""
        return self._is_speaking or self._current_state == AppState.SPEAKING
    
    @property
    def is_listening(self) -> bool:
        """Return True if currently listening for user input."""
        return self._is_listening or self._current_state == AppState.LISTENING
    
    @property
    def is_active(self) -> bool:
        """Return True if in an active session (not idle/ready)."""
        return self._current_state not in {AppState.IDLE, AppState.READY, AppState.ERROR}
    
    @property
    def time_in_current_state(self) -> float:
        """Return seconds spent in current state."""
        return time.time() - self._state_entered_time
    
    @property
    def is_turn_complete(self) -> bool:
        """Return True if the current turn is marked complete."""
        return self._is_turn_complete
    
    @property
    def expecting_followup(self) -> bool:
        """Return True if waiting for a follow-up response."""
        return self._expecting_followup
    
    @property
    def is_tool_active(self) -> bool:
        """Return True if a tool is currently executing."""
        return self._is_tool_active
    
    def set_callback(self, on_state_change: Callable) -> None:
        """Set the state change callback after initialization."""
        self._on_state_change = on_state_change
    
    async def transition_to(self, new_state: str, force: bool = False) -> bool:
        """
        Transition to a new state.
        
        Args:
            new_state: Target state (string or AppState)
            force: If True, skip transition validation
            
        Returns:
            True if transition succeeded, False if blocked
        """
        # Convert string to enum if needed
        if isinstance(new_state, str):
            try:
                target = AppState(new_state)
            except ValueError:
                logger.warning(f"[ STATE ] : Invalid state '{new_state}'")
                return False
        else:
            target = new_state
        
        # Skip if already in target state
        if target == self._current_state:
            return True
        
        # Validate transition (unless forced)
        if not force:
            valid_targets = self.VALID_TRANSITIONS.get(self._current_state, set())
            if target not in valid_targets:
                logger.debug(f"[ STATE ] : Blocked invalid transition {self._current_state.value} -> {target.value}")
                return False
        
        # Perform transition
        old_state = self._current_state
        self._previous_state = old_state
        self._current_state = target
        self._state_entered_time = time.time()
        
        # Update convenience flags
        self._is_speaking = (target == AppState.SPEAKING)
        self._is_listening = (target == AppState.LISTENING)
        
        # Log transition
        logger.debug(f"[ STATE ] : Transitioning {old_state.value} -> {target.value}")
        
        # Add to history (keep last 10)
        self._state_history.append({
            "from": old_state.value,
            "to": target.value,
            "time": time.time()
        })
        if len(self._state_history) > 10:
            self._state_history.pop(0)
        
        # Fire callback
        if self._on_state_change:
            await self._on_state_change(target.value)
        
        return True
    
    def reset_turn_tracking(self) -> None:
        """Reset turn-level tracking flags."""
        self._is_turn_complete = False
        self._expecting_followup = False
        self._last_activity_time = time.time()
    
    def mark_turn_complete(self) -> None:
        """Mark the current turn as complete."""
        self._is_turn_complete = True
    
    def set_expecting_followup(self, expecting: bool) -> None:
        """Set whether a followup question is expected."""
        self._expecting_followup = expecting
    
    def set_tool_active(self, active: bool) -> None:
        """Set whether a tool is currently executing."""
        self._is_tool_active = active
    
    def update_activity_time(self) -> None:
        """Update the last activity timestamp."""
        self._last_activity_time = time.time()
    
    def get_debug_info(self) -> dict:
        """Return debug information about current state."""
        return {
            "current_state": self._current_state.value,
            "previous_state": self._previous_state.value,
            "is_speaking": self.is_speaking,
            "is_listening": self.is_listening,
            "is_active": self.is_active,
            "is_turn_complete": self._is_turn_complete,
            "expecting_followup": self._expecting_followup,
            "time_in_state": round(self.time_in_current_state, 2),
            "recent_history": self._state_history[-5:]
        }

    async def evaluate_closure(self, 
                             source: str, 
                             is_active_fn: Callable[[], bool],
                             network_done_fn: Callable[[], bool],
                             audio_done_fn: Callable[[], bool],
                             stop_session_fn: Callable[[], Any]) -> bool:
        """
        Evaluate if the session should close.
        
        Logic moved from SessionManager to centralize session lifecycle.
        """
        if not is_active_fn():
            return False

        network_done = self._is_turn_complete or network_done_fn()
        audio_done = not self._is_speaking and audio_done_fn()
        tool_pending = self._is_tool_active
        followup_expected = self._expecting_followup or self._pending_clarification
        
        if self._interrupt_active:
            logger.debug("[ STATE ] : Interrupt active - skipping closure evaluation")
            return False
            
        should_close = network_done and audio_done and not tool_pending and not followup_expected and not self._agentic_veto
        
        if should_close:
            logger.info(f"[ STATE ] : All conditions met ({source}). Closing session.")
            await stop_session_fn()
            await self.transition_to(AppState.READY)
            
            # Reset flags
            self._is_turn_complete = False
            self._expecting_followup = False
            self._pending_clarification = False
            return True
            
        return False

    async def handle_audio_finished(self, 
                                  is_listening: bool, 
                                  unmute_fn: Callable[[], Any],
                                  evaluate_closure_fn: Callable[[str], Any]) -> None:
        """Handle audio playback completion."""
        if self._is_speaking:
            if is_listening:
                target_state = AppState.THINKING if self._is_tool_active else AppState.ACTIVE
                await self.transition_to(target_state)
            
            # Hardware unmute
            unmute_fn()
            
            # Try closure
            await evaluate_closure_fn("audio_finished")
