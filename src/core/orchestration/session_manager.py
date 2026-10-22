"""
Session Manager - Orchestrates AI conversations
Manages conversation sessions, routes to appropriate models, and executes tools.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Any, Optional, List, Tuple, TYPE_CHECKING
import logging
import time

if TYPE_CHECKING:
    from src.core.audio.audio_handler import AudioHandler
    from src.infrastructure.tools import ToolRegistry

# from src.core.intelligence.model_router import ModelRouter
# from src.core.intelligence.memory_engine import MemoryEngine
# from src.core.tools.base_executor import BaseToolExecutor
# from src.core.tools.tool_executor import LocalToolExecutor as ToolExecutor
# from src.core.orchestration.state_coordinator import StateCoordinator
# from src.core.orchestration.coordinators.model_coordinator import ModelCoordinator
# from src.core.orchestration.coordinators.tool_coordinator import ToolCoordinator
# from src.core.orchestration.coordinators.voice_coordinator import VoiceCoordinator
# from src.core.orchestration.coordinators.watchdog_coordinator import WatchdogCoordinator
# from src.core.orchestration.coordinators.gui_coordinator import GUICoordinator
# from src.core.tools.mcp_registry import MCPRegistry
# from src.core.intelligence.background_agents import BackgroundAgentManager
# from src.core.intelligence.prompt_builder import SystemPromptBuilder
#interfaces.overlay_manager import OverlayManager
from src.shared.exceptions import VoiceAssistantError
from src.shared.logger import setup_logger
from src.shared.debug_helper import debug_print
from models.base_model import BaseModel



# EventBus for decoupled communication
from src.application.events import (
    EventBus,
    ToolStarted,
    ToolCompleted,
    ReasoningEvent,
)

logger = setup_logger("SessionManager")
VAD_THROUGH_THRESHOLD = 0.01



class SessionManager:
    """
    Manages conversation sessions with the AI assistant.
    Coordinates between audio I/O, models, and tools.
    """
    
    @property
    def current_state(self) -> str: return self.state.current_state
    # NOTE: is_listening / is_speaking are defined once, later in the class (they
    # delegate to the active session with a StateCoordinator fallback). Do not add
    # shadowing copies here — the earlier duplicates were dead code.
    @property
    def _is_speaking(self) -> bool: return self.state.is_speaking
    @property
    def _expecting_followup(self) -> bool: return self.state.expecting_followup
    @_expecting_followup.setter
    def _expecting_followup(self, value: bool) -> None: self.state.set_expecting_followup(value)
    @property
    def _is_turn_complete(self) -> bool: return self.state.is_turn_complete
    @_is_turn_complete.setter
    def _is_turn_complete(self, value: bool) -> None:
        if value: self.state.mark_turn_complete()
        else: self.state.reset_turn_tracking()
    @property
    def _is_tool_active(self) -> bool: return self.state.is_tool_active
    @_is_tool_active.setter
    def _is_tool_active(self, value: bool) -> None: self.state.set_tool_active(value)
    
    def __init__(
        self,
        config: Dict[str, Any],
        model_router: Any,
        tool_registry: Any,
        audio_handler: Optional[Any] = None,
        memory_engine: Optional[Any] = None,
        wake_word_detector: Optional[Any] = None,
        tool_executor: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        app_reference: Optional[Any] = None,
    ):
        from src.core.orchestration.state_coordinator import StateCoordinator
        from src.core.utils.consolidation_manager import ConsolidationManager
        from src.core.tools.tool_executor import LocalToolExecutor as ToolExecutor
        from src.core.orchestration.coordinators.tool_coordinator import ToolCoordinator
        from src.core.tools.mcp_registry import MCPRegistry
        from src.core.interfaces.overlay_manager import OverlayManager

        self.config = config
        self.router = model_router
        self.tools = tool_registry
        self.audio = audio_handler
        if memory_engine is None:
            from src.core.intelligence.memory_engine import MemoryEngine
            self.memory = MemoryEngine()
        else:
            self.memory = memory_engine
        self.wake_word_detector = wake_word_detector
        self.app_reference = app_reference
        # Active model is only established once a (voice) persistent session starts;
        # initialise it so closure evaluation on the voice path can't AttributeError.
        self.active_model = None


        self._interrupt_task: Optional[asyncio.Task] = None

        # EventBus for decoupled communication
        self.event_bus = event_bus or EventBus()
        
        # Initialize Consolidation Manager (Auto-Sleep Protocol)
        self.consolidation_manager = ConsolidationManager(
            memory_engine=self.memory,
            profile=config.get('context_budget_profile', 'balanced')
        )
        
        # Intelligent Watchdog State
        self._last_ai_activity_time: float = time.time()
        logger.debug("[ SYSTEM ] : Intelligent Watchdog armed. Monitoring activity vs tool-state.")
        
        # State Coordinator (centralized state machine)
        self.state = StateCoordinator()
        
        # All session-level state is now managed by StateCoordinator or specialized coordinators
        self._session_lock: asyncio.Lock = asyncio.Lock()
        self._session_start_time: Optional[float] = None
        
        # Turn-level tracking (now primarily managed by StateCoordinator)
        self._audio_buffer: List[bytes] = []
        self._processed_tool_ids: set = set()
        self._active_tool_tasks: set[asyncio.Task] = set()
        
        # Initialize tool executor (DI Support)
        if tool_executor:
            self.tool_executor = tool_executor
        else:
            self.tool_executor = ToolExecutor(
                tool_registry=tool_registry,
                on_tool_start=self._on_tool_start_wrapper,
                on_tool_end=self._on_tool_end_wrapper,
                app_reference=app_reference,
                memory_engine=self.memory
            )
        
        # Phase 2: Initialize ToolCoordinator (NEW - scalable tool execution)
        # This will gradually replace direct tool_executor usage
        self.tool_coordinator = ToolCoordinator(
            tool_registry=self.tools,
            tool_executor=self.tool_executor,
            event_bus=self.event_bus,
            confirmation_handler=self._confirm_tool_execution,
            interrupt_check=lambda: getattr(self, '_interrupt_active', False)
        )

        from src.core.orchestration.coordinators.model_coordinator import ModelCoordinator
        self.model_coordinator = ModelCoordinator(
            model_router=self.router,
            event_bus=self.event_bus,
            consolidation_manager=self.consolidation_manager
        )

        # Lazy-load ConversationOrchestrator to prevent top-level PyTorch/LangGraph imports
        from src.core.orchestration.conversation_orchestrator import ConversationOrchestrator
        self.conversation_orchestrator = ConversationOrchestrator(
            model_coordinator=self.model_coordinator,
            tool_coordinator=self.tool_coordinator,
            state_coordinator=self.state,
            memory_engine=self.memory,
            event_bus=self.event_bus,
            config=self.config
        )

        if self.audio:
            from src.core.orchestration.coordinators.voice_coordinator import VoiceCoordinator
            self.voice_coordinator = VoiceCoordinator(
                audio_handler=self.audio,
                active_model_fn=lambda: self.active_model,
                on_activity_time_updated_fn=self._update_activity_time
            )

            from src.core.orchestration.coordinators.watchdog_coordinator import WatchdogCoordinator
            self.watchdog_coordinator = WatchdogCoordinator(
                wake_word_detector=self.wake_word_detector,
                on_interrupt_detected=self._on_interrupt_detected,
                on_zombie_turn_recovered=self._on_zombie_recovery,
                on_neural_hang_recovered=self._on_neural_hang_recovery
            )

            # Initialize Overlay Manager for OS-level notifications
            self.overlay = OverlayManager(
                audio_handler=self.audio,
                sound_manager=config.get('sound_manager')
            )
            
            # LINK AUDIO OUTPUT TO SOUND MANAGER
            sound_manager = config.get('sound_manager')
            if sound_manager and hasattr(sound_manager, 'set_audio_output'):
                sound_manager.set_audio_output(self.audio.output)
                logger.debug("[ AUDIO ] : SoundManager linked to Hardware Output.")
            
            # Register Audio Playback Completion Handler (Push Architecture)
            self.audio.output.on_playback_complete = self._on_audio_playback_finished
        else:
            self.voice_coordinator = None
            self.watchdog_coordinator = None
            self.overlay = None
        
        # Memory synchronization tracker
        self._last_synced_facts: str = ""

        # Intelligence Stack (router_agent already initialized above)
        self.mcp_registry = MCPRegistry()
        self._pending_user_text: Optional[str] = None
        
        # Wire EventBus subscriptions for decoupled life-cycle management
        from src.application.events import AudioResponseReceived, ToolLimitConfirmationResponse
        self.event_bus.subscribe(AudioResponseReceived, self._on_audio_received_event)
        self.event_bus.subscribe(ToolLimitConfirmationResponse, self._on_tool_limit_confirmation_response)

        # Initialize SessionCoordinator
        from src.core.orchestration.session_coordinator import SessionCoordinator
        self.session_coordinator = SessionCoordinator(
            config=self.config,
            conversation_orchestrator=self.conversation_orchestrator,
            event_bus=self.event_bus,
            model_coordinator=self.model_coordinator,
            voice_coordinator=self.voice_coordinator,
            watchdog_coordinator=self.watchdog_coordinator,
            memory_engine=self.memory,
            tool_registry=self.tools,
            state_coordinator=self.state,
            tool_coordinator=self.tool_coordinator,
            audio_handler=self.audio,
            consolidation_manager=self.consolidation_manager,
        )

        logger.info("[ SUCCESS ] Session orchestration system active.")


    async def _on_audio_received_event(self, event: Any) -> None:
        """
        Handle AudioResponseReceived event from the EventBus.
        This keeps audio/voice-specific logic in SessionManager.
        """
        audio_data = event.audio_data

        # CRITICAL: Mute input IMMEDIATELY when AI audio starts to prevent feedback loop
        if event.mute_input:
            self.audio.input.mute_input()
            logger.debug("Microphone muted (AI audio started - preventing feedback loop)")

        if audio_data:
            # STREAM vs BUFFER decision:
            # - If tools have been executed this turn, STREAM immediately (low latency UX)
            # - If no tools yet, BUFFER (prevents double-speech if tool_call arrives later)
            if self.tool_coordinator.get_tool_count() > 0:
                # Tools completed - STREAM audio immediately for responsive UX
                if not self._is_speaking:
                    # _is_speaking is read-only (StateCoordinator derives it from the
                    # state machine); transitioning to "speaking" is what sets it.
                    await self._transition_to_state("speaking")
                    logger.info("[ AUDIO ] : Streaming post-tool audio (low-latency mode)")
                await self.audio.output.play(audio_data)
                self._audio_played_this_turn = True
            else:
                # No tools yet - buffer in case tool_call arrives
                self._audio_buffer.append(audio_data)
        else:
            logger.warning("[SESSION MANAGER]: Received AudioResponseReceived but audio_data is empty!")

    def reset_interrupt(self) -> None:
        """Surgically clear the interrupt flag for the next turn."""
        if getattr(self, '_interrupt_active', False):
            self._interrupt_active = False
            logger.info(f"[ INTERRUPT ] : Flag reset (surgical).")

    def set_agentic_veto(self, state: bool) -> None:
        """Set or clear the agentic veto. When True, session closure is blocked."""
        self._agentic_veto = state
        logger.info(f"[ VETO ] : Agentic veto {'ACTIVE' if state else 'CLEARED'}")
    

    
    
    async def _on_audio_playback_finished(self) -> None:
        """Hardware callback for audio finish. Delegated to StateCoordinator."""
        await self.state.handle_audio_finished(
            is_listening=self.is_listening,
            unmute_fn=self.audio.input.unmute_input,
            evaluate_closure_fn=self._evaluate_session_closure
        )

    async def _evaluate_session_closure(self, source: str = "unknown") -> bool:
        """Delegates closure evaluation to StateCoordinator."""
        return await self.state.evaluate_closure(
            source=source,
            is_active_fn=lambda: self.is_listening,
            network_done_fn=lambda: self._is_turn_complete or (self.active_model and getattr(self.active_model, "is_generation_complete", False)),
            audio_done_fn=lambda: not self.audio.output.is_playing,
            stop_session_fn=self.stop_conversation
        )


    async def _reset_turn_tracking(self) -> None:
        """
        Reset turn-level tracking variables.

        Phase 2: Delegates to ConversationOrchestrator for high-level turn tracking.
        SessionManager only resets voice-mode-specific state (audio, reasoning UI).
        """
        # Delegate to orchestrator for high-level turn management
        if hasattr(self, 'conversation_orchestrator') and self.conversation_orchestrator:
            await self.conversation_orchestrator._reset_turn_tracking()

        # Voice-mode-specific resets (not in orchestrator scope)
        self._audio_played_this_turn = False
        self._current_reasoning_text = ""
        self._tool_response_sent_this_turn = False
        self._processed_tool_ids = set()  # Deduplication for streaming protocol
        # NOTE: Do NOT reset _interrupt_active here - it must persist until finally block checks it

        # Publish reasoning clear event for UI
        self.event_bus.publish(ReasoningEvent(reasoning_text=""))

    async def _on_tool_start_wrapper(self, tool_name: str, params: Dict[str, Any], action_message: str, tool_id: str = None) -> None:
        """Wrapper for tool start callback."""
        self.event_bus.publish(ToolStarted(
            tool_name=tool_name,
            args=params,
            correlation_id=tool_id,
            display_text=action_message
        ))

    async def _on_tool_end_wrapper(self, tool_name: str, result: Dict[str, Any], tool_id: str = None) -> None:
        """Wrapper for tool end callback."""
        success = result.get('success', True) if isinstance(result, dict) else True
        error_msg = result.get('error') if isinstance(result, dict) else None

        self.event_bus.publish(ToolCompleted(
            tool_name=tool_name,
            result=result,
            success=success,
            error=error_msg,
            correlation_id=tool_id
        ))

    async def _transition_to_state(self, state: str) -> None:
        """
        Transition to a new UI state.
        Delegates to StateCoordinator while handling sound manager triggers.
        
        Args:
            state: Target state (ready, active, listening, thinking, speaking)
        """
        old_state = self.current_state
        # self._current_state is DEPRECATED - StateCoordinator holds truth
        
        
        # AUDIO STATE TRIGGERS (pre-transition side effects)
        sound_manager = self.config.get('sound_manager')
        
        if state == "thinking":
            # Reset AI activity timer when we START thinking 
            self._last_ai_activity_time = time.time()
            self.state.update_activity_time()
            
            if sound_manager:
                try:
                    # sound_manager.start_thinking_loop()
                    pass
                except Exception as e:
                    logger.debug(f"Failed to start thinking loop: {e}")
        elif state in ["speaking", "ready", "listening", "active"]:
            if sound_manager:
                try:
                    # sound_manager.stop_thinking_loop()
                    pass
                except Exception as e:
                    logger.debug(f"Failed to stop thinking loop: {e}")
        
        # Delegate to StateCoordinator (which fires the callback)
        await self.state.transition_to(state, force=True)

    # ========================================================================
    # ToolCoordinator Helper Methods (Phase 2: Milestone 2.1)
    # ========================================================================



    async def _confirm_tool_execution(
        self,
        function_calls: List[Dict[str, Any]],
        current_tool_count: int
    ) -> Tuple[bool, int]:
        """
        Request user confirmation for tool execution (soft limit reached).
        Called by ToolCoordinator when soft limit is exceeded.

        Args:
            function_calls: List of tool calls pending execution
            current_tool_count: Current total tool count this turn

        Returns:
            Tuple of (should_proceed: bool, new_tool_count: int)
        """
        import asyncio
        import uuid
        from src.application.events import ToolLimitConfirmationRequest

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create a future for user response
        response_future = asyncio.Future()

        # Store future for when response arrives
        if not hasattr(self, '_tool_confirmation_futures'):
            self._tool_confirmation_futures = {}

        self._tool_confirmation_futures[request_id] = response_future

        # Publish confirmation request event to EventBus
        self.event_bus.publish(ToolLimitConfirmationRequest(
            function_calls_count=len(function_calls),
            current_tool_count=current_tool_count,
            request_id=request_id
        ))

        logger.info(
            f"[ TOOL_COORDINATOR ] Requesting user confirmation via EventBus: "
            f"{len(function_calls)} tools, {current_tool_count} total (request_id={request_id})"
        )

        try:
            # Wait for user response with timeout
            user_approved = await asyncio.wait_for(response_future, timeout=30.0)

            if user_approved:
                logger.info("[ TOOL_COORDINATOR ] User approved tool execution")
                return (True, current_tool_count)
            else:
                logger.info("[ TOOL_COORDINATOR ] User declined tool execution")
                return (False, current_tool_count)

        except asyncio.TimeoutError:
            logger.warning("[ TOOL_COORDINATOR ] Confirmation timeout - defaulting to DECLINE")
            return (False, current_tool_count)
        finally:
            # Cleanup future
            if request_id in self._tool_confirmation_futures:
                del self._tool_confirmation_futures[request_id]

    def _on_tool_limit_confirmation_response(self, event) -> None:
        """
        Handle ToolLimitConfirmationResponse event from GUI.
        Resolves the corresponding future to unblock tool execution.

        Args:
            event: ToolLimitConfirmationResponse with request_id and approved flag
        """
        if not hasattr(self, '_tool_confirmation_futures'):
            return

        request_id = event.request_id
        if request_id in self._tool_confirmation_futures:
            future = self._tool_confirmation_futures[request_id]
            if not future.done():
                # Resolve the future with user's decision
                future.set_result(event.approved)
                logger.info(f"[ TOOL_COORDINATOR ] User response received: {'APPROVED' if event.approved else 'DECLINED'} (request_id={request_id})")

    # ========================================================================

    
    async def _start_persistent_gemini_session(self) -> bool:
        """
        Start the persistent Gemini WebSocket session on app startup.
        This eliminates the 3-second cold start when wake word is detected.

        Phase 2: Uses ModelCoordinator (PRIMARY PATH) with legacy fallback.
        """
        debug_print("DEBUG: Initializing persistent Gemini session...")

        USE_MODEL_COORDINATOR = True  # Feature flag for gradual migration

        try:
            if USE_MODEL_COORDINATOR and hasattr(self, 'model_coordinator'):
                # Phase 2: Use ModelCoordinator (PRIMARY PATH)
                logger.debug("[ SESSION ] Using ModelCoordinator for persistent session")

                # Select model for conversation
                model = await self.model_coordinator.select_model(category='chat')

                if not model:
                    logger.error("[ SESSION ] Failed to select conversation model")
                    return False

                # Sync with legacy active_model for backward compatibility
                self.active_model = model

                # Get tool schemas
                tool_schemas = self.model_coordinator.get_tool_schemas(
                    tool_registry=self.tools,
                    category=None  # Load all tools
                )
                logger.info(f"[ TOOLS ] : Loaded {len(tool_schemas)} tools for persistent session")

                # Start persistent session via coordinator
                success = await self.model_coordinator.start_persistent_session(tool_schemas)

                if success:
                    debug_print("DEBUG: Persistent Gemini session established!")
                    logger.warning("[ SYSTEM ] : Persistent session active. Wake word response will be instant.")
                    return True
                else:
                    debug_print("DEBUG: Failed to start persistent session - will use ephemeral mode")
                    logger.warning("[ SYSTEM ] : Persistent session failed. Using ephemeral mode.")
                    return False

            else:
                # Should never reach here - model_coordinator should always exist
                logger.error("[ SESSION ] ModelCoordinator not available")
                return False

        except Exception as e:
            debug_print(f"DEBUG: Error starting persistent session: {e}")
            logger.error(f"Failed to start persistent session: {e}", exc_info=True)
            return False

    async def enable_voice_mode(self) -> bool:
        """
        Enable Voice Mode: Establish persistent WebSocket connection.
        Called when user toggles Voice Mode ON.
        """
        logger.info("[ SESSION ] : Enabling Voice Mode (Establishing Uplink)...")
        
        # Ensure hardware input is running
        if self.audio and hasattr(self.audio, 'start_input') and not self.audio.input.is_active:
             logger.info("[ SESSION ] : Text Mode -> Voice Mode: Starting Hardware Input...")
             try:
                 self.audio.start_input()
             except Exception as e:
                 logger.error(f"Failed to start audio input: {e}")
                 self.show_notification("Audio Error", "Microphone unavailable.")
                 return False

        return await self._start_persistent_gemini_session()

    async def disable_voice_mode(self) -> None:
        """
        Disable Voice Mode: Close persistent WebSocket connection.
        Called when user toggles Voice Mode OFF.

        Phase 2: Uses ModelCoordinator (PRIMARY PATH) with legacy fallback.
        """
        logger.info("[ SESSION ] : Disabling Voice Mode (Closing Uplink)...")

        USE_MODEL_COORDINATOR = True  # Feature flag

        # 1. Close the active model's session if it exists
        if USE_MODEL_COORDINATOR and hasattr(self, 'model_coordinator'):
            # Phase 2: Use ModelCoordinator (PRIMARY PATH)
            await self.model_coordinator.close_persistent_session()
        elif self.active_model and hasattr(self.active_model, 'close_persistent_session'):
            # Legacy path (FALLBACK)
            await self.active_model.close_persistent_session()

        # 2. Reset active model reference (optional, but cleaner)
        # We don't fully clear self.active_model as it might retain helpful config,
        # but we definitely want to ensure no background tasks are running.

        # 3. Stop Hardware Input (Save resources / Text Mode)
        if self.audio and self.audio.input.is_active:
            logger.info("[ SESSION ] : Voice Mode -> Text Mode: Stopping Hardware Input...")
            self.audio.input.stop()

        logger.info("[ SESSION ] : Voice Mode DISABLED.")

    @property
    def is_listening(self) -> bool:
        if self.session_coordinator.active_session and hasattr(self.session_coordinator.active_session, 'is_listening'):
            return self.session_coordinator.active_session.is_listening
        return self.state.is_active

    @is_listening.setter
    def is_listening(self, value: bool) -> None:
        if self.session_coordinator.active_session and hasattr(self.session_coordinator.active_session, 'is_listening'):
            self.session_coordinator.active_session.is_listening = value

    @property
    def is_speaking(self) -> bool:
        if self.session_coordinator.active_session and hasattr(self.session_coordinator.active_session, 'is_speaking'):
            return self.session_coordinator.active_session.is_speaking
        return self._is_speaking

    async def start_conversation(self, category: str = 'chat') -> None:
        """
        Start a conversation session (persistent VoiceSession).
        """
        await self.session_coordinator.start_voice_session(category)

    async def inject_text_interaction(self, text: str, category: str = 'chat') -> None:
        """
        Inject a text interaction.
        """
        logger.info(f"Injecting text interaction: {text}")
        await self.execute_text_command(text, category)

    async def start_modules(self) -> None:
        """Starts background intelligence modules."""
        logger.info("[ SYSTEM ] : Session Intelligence Modules READY.")

    async def start_intelligence(self) -> None:
        """Initialize and start intelligence engines."""
        pass

    def _update_activity_time(self, can_reset_followup: bool = True) -> None:
        """Update last activity timestamp."""
        self._last_ai_activity_time = time.time()
        self.state.update_activity_time()

    async def _on_zombie_recovery(self) -> None:
        logger.warning("[ WATCHDOG ] : Zombie turn recovery triggered.")
        await self.stop_active_turn()

    async def _on_neural_hang_recovery(self) -> None:
        logger.warning("[ WATCHDOG ] : Neural hang recovery triggered.")
        await self.stop_active_turn()

    async def _on_interrupt_detected(self) -> None:
        logger.warning("[ WATCHDOG ] : Interrupt detected.")
        await self.stop_active_turn()

    async def _handle_response(self, response: Dict[str, Any]) -> None:
        pass

    async def handle_interruption(self, target_state: str = "listening") -> None:
        """Trigger interruption protocol."""
        if self.session_coordinator.active_session and hasattr(self.session_coordinator.active_session, 'handle_interruption'):
            await self.session_coordinator.active_session.handle_interruption(target_state)

    async def stop_active_turn(self) -> None:
        """Stop current active turn."""
        await self.session_coordinator.stop_active_turn()

    def stop_conversation(self) -> None:
        """Stop conversation session (sync)."""
        self.session_coordinator.stop_conversation()

    async def async_stop_conversation(self, force_close_socket: bool = True) -> None:
        """Stop conversation session (async)."""
        await self.session_coordinator.async_stop_conversation(force_close_socket)

    async def execute_text_command(self, command: str, category: str = 'chat') -> Dict[str, Any]:
        """Execute a turn-based text command."""
        return await self.session_coordinator.execute_text_command(command, category)

    async def cleanup(self) -> None:
        """Clean up session resources."""
        await self.session_coordinator.cleanup()
        if hasattr(self, 'conversation_orchestrator') and self.conversation_orchestrator:
            await self.conversation_orchestrator.cleanup()
        await self.router.cleanup()
        if hasattr(self, 'memory'):
            self.memory.close()
        if hasattr(self, 'mcp_registry') and self.mcp_registry:
            await self.mcp_registry.cleanup()

    def show_notification(self, title: str, message: str, icon: str = "⚠", duration: int = 10) -> None:
        """Delegates notification display to GUICoordinator."""
        if hasattr(self, 'gui_coordinator') and self.gui_coordinator:
            self.gui_coordinator.show_notification(title, message, icon=icon, duration=duration)
        else:
            logger.info(f"Notification: [{title}] {message}")
    # End of SessionManager
