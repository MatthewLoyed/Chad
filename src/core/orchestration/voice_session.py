"""
Voice Session - Manages the lifecycle of persistent voice interaction.
Extracted from SessionManager.
"""

import asyncio
import time
import logging
from typing import Dict, Any, Optional, List, Tuple

from src.shared.exceptions import VoiceAssistantError
from src.shared.logger import setup_logger
from src.shared.debug_helper import debug_print
from src.application.events import (
    EventBus,
    ThinkingEvent,
    ReasoningEvent,
    TranscriptEvent,
    GroundingMetadataEvent,
    AudioResponseReceived,
)

logger = setup_logger("VoiceSession")
logger.setLevel(logging.WARNING)

class VoiceSession:
    """
    Manages a single active voice session.
    Handles hardware mic/output coordination, VAD activity tracking, watchdogs,
    and persistent WebSocket audio streaming to the AI model.
    """
    def __init__(
        self,
        config: Dict[str, Any],
        model_coordinator: Any,
        voice_coordinator: Any,
        watchdog_coordinator: Any,
        event_bus: EventBus,
        memory_engine: Any,
        tool_registry: Any,
        state_coordinator: Any,
        tool_coordinator: Any,
        audio_handler: Any,
        consolidation_manager: Any,
        conversation_orchestrator: Any,
    ):
        self.config = config
        self.model_coordinator = model_coordinator
        self.voice_coordinator = voice_coordinator
        self.watchdog_coordinator = watchdog_coordinator
        self.event_bus = event_bus
        self.memory = memory_engine
        self.tools = tool_registry
        self.state = state_coordinator
        self.tool_coordinator = tool_coordinator
        self.audio = audio_handler
        self.consolidation_manager = consolidation_manager
        self.conversation_orchestrator = conversation_orchestrator

        # Silence Guard (Optional attribute, defaults to None)
        self.silence_guard = None

        # Task handles
        self._timeout_monitor_task: Optional[asyncio.Task] = None
        self._silence_guard_task: Optional[asyncio.Task] = None
        self._interrupt_task: Optional[asyncio.Task] = None
        self._streaming_task: Optional[asyncio.Task] = None

        # Session state variables
        self.active_model = None
        self.current_category = 'chat'
        self.is_listening = False
        self._session_start_time: Optional[float] = None
        self._last_ai_activity_time: float = time.time()
        self._audio_played_this_turn = False
        self._interrupt_active = False
        self._turn_active = False
        self._pending_clarification = False
        self._expecting_followup = False
        self._agentic_veto = False
        self._last_synced_facts = ""

        # Subscriptions
        self._event_bus_handlers = []
        self._subscribe_events()

    @property
    def current_state(self) -> str:
        return self.state.current_state

    @property
    def _is_speaking(self) -> bool:
        return self.state.is_speaking

    @property
    def _is_turn_complete(self) -> bool:
        return self.state.is_turn_complete

    @property
    def _is_tool_active(self) -> bool:
        return self.state.is_tool_active

    def _subscribe_events(self):
        """Wire EventBus subscriptions for the session."""
        self.event_bus.subscribe(AudioResponseReceived, self._on_audio_received_event)

        # Track subscriptions for cleanup
        self._event_bus_handlers.append((AudioResponseReceived, self._on_audio_received_event))

    def _unsubscribe_events(self):
        """Unsubscribe all wired events."""
        for event_type, handler in self._event_bus_handlers:
            try:
                self.event_bus.unsubscribe(event_type, handler)
            except Exception as e:
                logger.warning(f"Error unsubscribing handler: {e}")
        self._event_bus_handlers.clear()

    async def _on_audio_received_event(self, event: Any) -> None:
        audio_data = event.audio_data

        if event.mute_input and self.audio and hasattr(self.audio, 'input'):
            self.audio.input.mute_input()
            logger.debug("Microphone muted (AI audio started)")

        if audio_data and self.audio and hasattr(self.audio, 'output'):
            if self.tool_coordinator.get_tool_count() > 0:
                if not self._is_speaking:
                    await self._transition_to_state("speaking")
                    logger.info("[ AUDIO ] : Streaming post-tool audio (low-latency mode)")
                await self.audio.output.play(audio_data)
                self._audio_played_this_turn = True
            else:
                self._audio_buffer.append(audio_data)
        elif not audio_data:
            logger.warning("[VOICE SESSION]: Received AudioResponseReceived but audio_data is empty!")

    async def start(self, category: str = 'chat') -> None:
        """Start the conversation session loop."""
        self.is_listening = True
        await self._transition_to_state("listening")

        if self.audio and hasattr(self.audio, 'input'):
            try:
                if hasattr(self.audio.input, 'clear_queue'):
                    self.audio.input.clear_queue()
            except Exception as e:
                logger.warning(f"Failed to clear audio input queue: {e}")

            try:
                self.audio.input.unmute_input()
                logger.debug("Microphone ACTIVE for new conversation.")
            except Exception as e:
                logger.warning(f"Failed to unmute microphone at start: {e}")

        self._last_ai_activity_time = time.time()
        self._session_start_time = time.time()

        await self._reset_turn_tracking()
        self._interrupt_active = False
        self._turn_active = False

        self.state.reset_turn_tracking()
        is_followup_continuation = self._expecting_followup
        if not is_followup_continuation:
            self.state.set_expecting_followup(False)
        self._pending_clarification = False

        try:
            self.current_category = category
            
            # Retrieve persistent session active model (already established via model_coordinator)
            self.active_model = getattr(self.model_coordinator, 'active_session_model', None) or await self.model_coordinator.select_model(category=category)
            if not self.active_model:
                self.active_model = self.model_coordinator.router.route(category=category)

            if not is_followup_continuation and self.active_model and hasattr(self.active_model, 'expecting_followup'):
                self.active_model.expecting_followup = False

            if self.active_model and hasattr(self.active_model, 'reset_generation_flag'):
                self.active_model.reset_generation_flag()

            if self.active_model and hasattr(self.active_model, 'set_consolidation_manager'):
                self.active_model.set_consolidation_manager(self.consolidation_manager)

            # Load tool schemas
            tool_schemas = self.tools.get_all_schemas() if category == 'chat' else self.model_coordinator.router.get_tool_schemas_for_category(category)

            # Inject memory/profile facts block
            facts_block = self.memory.get_memory_block()
            memory_changed = (facts_block != self._last_synced_facts)

            if self.active_model and hasattr(self.active_model, 'config'):
                new_instruction = facts_block
                current_instruction = self.active_model.config.get('system_instruction', '')
                if new_instruction != current_instruction:
                    self.active_model.config['system_instruction'] = new_instruction
                    logger.info("[ MEMORY ] System instruction REBUILT with latest facts")

            self._last_synced_facts = facts_block

            # Push mid-session memory update if needed
            if memory_changed and self.active_model and hasattr(self.active_model, 'is_session_active') and self.active_model.is_session_active():
                logger.info("[ MEMORY ] Mid-session memory update detected. Pushing to Gemini...")
                try:
                    update_text = f"### SYSTEM UPDATE: MEMORY CONSOLIDATION\nYour long-term memories have been updated. If this contradicts previous info, use this:\n\n{facts_block}"
                    from google.genai import types as genai_types
                    client_content = genai_types.LiveClientContent(
                        turns=[genai_types.Content(role='user', parts=[genai_types.Part(text=update_text)])],
                        turn_complete=False
                    )
                    asyncio.create_task(self.active_model.session.send(input=client_content))
                except Exception as e:
                    logger.warning(f"Failed to push mid-session memory update: {e}")

            # Create audio input generator with activity tracking
            audio_generator = self.voice_coordinator.create_activity_tracked_audio_generator(
                is_listening_fn=lambda: self.is_listening,
                session_start_time=self._session_start_time,
                is_audio_played_fn=lambda: self._audio_played_this_turn
            )

            # Start background monitoring
            self._timeout_monitor_task = asyncio.create_task(
                self.watchdog_coordinator.monitor_timeout(
                    is_listening_fn=lambda: self.is_listening,
                    get_last_ai_activity_time=lambda: self._last_ai_activity_time,
                    is_audio_played_fn=lambda: self._audio_played_this_turn,
                    is_audio_playing_fn=lambda: self.audio.output.is_playing if self.audio and hasattr(self.audio, 'output') else False,
                    is_tool_active_fn=lambda: self._is_tool_active,
                    get_current_state=lambda: self.current_state,
                    get_diagnostic_info=lambda: f"TurnComplete={self._is_turn_complete}, ToolActive={self._is_tool_active}, ExpectingFollowup={self._expecting_followup}, Speaking={self._is_speaking}"
                )
            )

            if self.silence_guard:
                self._silence_guard_task = asyncio.create_task(
                    self.silence_guard.monitor_loop(
                        is_listening_fn=lambda: self.is_listening,
                        is_model_connected_fn=lambda: (self.active_model is not None and getattr(self.active_model, "is_connected", False)),
                        is_speaking_fn=lambda: self._is_speaking,
                        is_tool_active_fn=lambda: self._is_tool_active,
                        audio_played_fn=lambda: self._audio_played_this_turn
                    )
                )

            if self.watchdog_coordinator.wake_word_detector:
                self._interrupt_task = asyncio.create_task(
                    self.watchdog_coordinator.monitor_interrupt(lambda: self.is_listening)
                )

            # Open audio gate
            if hasattr(self.active_model, 'resume_streaming'):
                self.active_model.resume_streaming()
                debug_print("DEBUG: Audio gate OPENED - using persistent session")

            # Stream loop
            self._streaming_task = asyncio.current_task()
            try:
                async for response in self.active_model.stream_audio(audio_generator, tool_schemas):
                    if response.get('type') == 'transcript' and not response.get('is_partial', False):
                        self._update_activity_time()
                    
                    await self._handle_response(response)
                    
                    if not self.is_listening:
                        logger.info("Session flag check: EXITTING conversation loop.")
                        break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in audio stream: {e}", exc_info=True)
                raise

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in conversation session: {e}", exc_info=True)
            raise VoiceAssistantError(f"Conversation failed: {e}")
        finally:
            self.stop()
            if self.audio and hasattr(self.audio, 'input'):
                self.audio.input.unmute_input()
            self._processed_tool_ids.clear()
            self.event_bus.publish(ReasoningEvent(reasoning_text=""))

    def stop(self) -> None:
        """Stop voice session streaming (without closing socket)."""
        if not self.is_listening:
            return

        self.is_listening = False
        self.model_coordinator.pause_streaming()

        if self._timeout_monitor_task: self._timeout_monitor_task.cancel()
        if self._silence_guard_task: self._silence_guard_task.cancel()
        if self._interrupt_task: self._interrupt_task.cancel()

        if self.audio and hasattr(self.audio, 'input'):
            try:
                self.audio.input.unmute_input()
            except: pass

    async def async_stop(self, force_close_socket: bool = True) -> None:
        """Stop voice session asynchronously."""
        self.stop()
        if force_close_socket and self.active_model and hasattr(self.active_model, 'force_close'):
            try:
                await self.active_model.force_close()
            except: pass

    async def stop_active_turn(self) -> None:
        """Interrupt active voice turn."""
        logger.warning("[ STOP ] : Interrupting active voice session...")
        await self.handle_interruption(target_state="ready")

    async def handle_interruption(self, target_state: str = "listening") -> None:
        """Trigger hardware interruption protocols."""
        if getattr(self, '_interrupt_active', False):
            return

        self._interrupt_active = True
        logger.warning(f"[ INTERRUPT ] : Interruption protocol triggered. Target state: {target_state}")

        # Transition UI to target state
        await self._transition_to_state(target_state)

        # Cancel any active WebSocket output/audio queues
        if self.active_model and hasattr(self.active_model, 'interrupt'):
            try:
                await self.active_model.interrupt()
            except Exception as e:
                logger.warning(f"Failed to interrupt active model: {e}")

        # Trigger hardware audio interrupt protocol
        if self.voice_coordinator:
            await self.voice_coordinator.execute_interrupt_protocol()

        logger.info(f"[ INTERRUPT ] : Protocol Complete. State: {target_state}")

    def reset_interrupt(self) -> None:
        self._interrupt_active = False

    def set_agentic_veto(self, state: bool) -> None:
        self._agentic_veto = state

    async def cleanup(self) -> None:
        """Clean up active session and subscriptions."""
        self.stop()
        self._unsubscribe_events()

    def _update_activity_time(self) -> None:
        self._last_ai_activity_time = time.time()
        self.state.update_activity_time()

    async def _reset_turn_tracking(self) -> None:
        self._audio_played_this_turn = False
        self._audio_buffer = []
        self._processed_tool_ids = set()
        self.event_bus.publish(ReasoningEvent(reasoning_text=""))

    async def _transition_to_state(self, state: str) -> None:
        await self.state.transition_to(state, force=True)

    async def _handle_response(self, response: Dict[str, Any]) -> None:
        """Handle response packets streaming from Gemini Live API."""
        r_type = response.get('type')
        
        # Route packet outputs to EventBus observers (e.g. desktop UI, Svelte frontend)
        if r_type == 'thinking':
            self.event_bus.publish(ThinkingEvent(thinking_text=response.get('text', '')))
        elif r_type == 'reasoning':
            self.event_bus.publish(ReasoningEvent(reasoning_text=response.get('text', '')))
        elif r_type == 'transcript':
            self.event_bus.publish(TranscriptEvent(
                transcript=response.get('text', ''),
                is_final=not response.get('is_partial', False)
            ))
        elif r_type == 'audio' and response.get('data'):
            # Trigger EventBus audio event
            self.event_bus.publish(AudioResponseReceived(
                audio_data=response.get('data'),
                mute_input=True
            ))
        elif r_type == 'tool_call':
            # Delegate tool execution via orchestrator/coordinator
            await self.conversation_orchestrator._handle_tool_call(response)
        elif r_type == 'tool_call_batch':
            await self.conversation_orchestrator._handle_tool_batch(response)
        elif r_type == 'turn_complete':
            await self._on_audio_playback_finished()
        elif r_type == 'grounding_metadata':
            self.event_bus.publish(GroundingMetadataEvent(metadata=response.get('metadata')))

    async def _on_audio_playback_finished(self) -> None:
        await self.state.handle_audio_finished(
            is_listening=self.is_listening,
            unmute_fn=self.audio.input.unmute_input if self.audio and hasattr(self.audio, 'input') else lambda: None,
            evaluate_closure_fn=self._evaluate_session_closure
        )

    async def _evaluate_session_closure(self, source: str = "unknown") -> bool:
        return await self.state.evaluate_closure(
            source=source,
            is_active_fn=lambda: self.is_listening,
            network_done_fn=lambda: self._is_turn_complete or (self.active_model and getattr(self.active_model, "is_generation_complete", False)),
            audio_done_fn=lambda: not self.audio.output.is_playing if self.audio and hasattr(self.audio, 'output') else True,
            stop_session_fn=self.stop
        )
