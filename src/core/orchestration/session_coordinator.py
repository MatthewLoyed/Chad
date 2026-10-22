"""
Session Coordinator - Manages and delegates to active sessions (VoiceSession or TextSession).
Extracted from SessionManager.
"""

import logging
from typing import Dict, Any, Optional, Union
from src.core.orchestration.voice_session import VoiceSession
from src.core.orchestration.text_session import TextSession
from src.application.events import EventBus
from src.shared.exceptions import VoiceAssistantError

logger = logging.getLogger("SessionCoordinator")
logger.setLevel(logging.WARNING)

class SessionCoordinator:
    """
    Coordinates between standard turn-based text sessions and persistent voice sessions.
    Decoupled from hardware-level details, delegating execution to the active session.
    """
    def __init__(
        self,
        config: Dict[str, Any],
        conversation_orchestrator: Any,
        event_bus: EventBus,
        model_coordinator: Any,
        voice_coordinator: Optional[Any] = None,
        watchdog_coordinator: Optional[Any] = None,
        memory_engine: Any = None,
        tool_registry: Any = None,
        state_coordinator: Any = None,
        tool_coordinator: Any = None,
        audio_handler: Optional[Any] = None,
        consolidation_manager: Any = None,
    ):
        self.config = config
        self.conversation_orchestrator = conversation_orchestrator
        self.event_bus = event_bus
        self.model_coordinator = model_coordinator
        self.voice_coordinator = voice_coordinator
        self.watchdog_coordinator = watchdog_coordinator
        self.memory = memory_engine
        self.tools = tool_registry
        self.state = state_coordinator
        self.tool_coordinator = tool_coordinator
        self.audio = audio_handler
        self.consolidation_manager = consolidation_manager

        # Active session reference
        self.active_session: Optional[Union[VoiceSession, TextSession]] = None

    async def start_voice_session(self, category: str = 'chat') -> None:
        """Initialize and run a persistent VoiceSession."""
        if not self.audio or not self.voice_coordinator:
            raise VoiceAssistantError("Audio hardware is not initialized. Cannot start voice session.")
            
        await self.cleanup_active_session()

        logger.info("[ COORDINATOR ] Initiating new VoiceSession...")
        voice_sess = VoiceSession(
            config=self.config,
            model_coordinator=self.model_coordinator,
            voice_coordinator=self.voice_coordinator,
            watchdog_coordinator=self.watchdog_coordinator,
            event_bus=self.event_bus,
            memory_engine=self.memory,
            tool_registry=self.tools,
            state_coordinator=self.state,
            tool_coordinator=self.tool_coordinator,
            audio_handler=self.audio,
            consolidation_manager=self.consolidation_manager,
            conversation_orchestrator=self.conversation_orchestrator,
        )
        self.active_session = voice_sess
        # Run the voice session (this blocks/streams)
        await voice_sess.start(category=category)

    async def start_text_session(self) -> TextSession:
        """Initialize and run a standard TextSession."""
        if not self.active_session or not isinstance(self.active_session, TextSession):
            await self.cleanup_active_session()
            logger.info("[ COORDINATOR ] Initiating new TextSession...")
            self.active_session = TextSession(
                conversation_orchestrator=self.conversation_orchestrator,
                event_bus=self.event_bus
            )
            await self.active_session.start()
        return self.active_session

    async def execute_text_command(self, command: str, category: str = 'chat') -> Dict[str, Any]:
        """Route text command execution to active/new TextSession."""
        text_session = await self.start_text_session()
        return await text_session.execute_command(command, category=category)

    async def stop_active_turn(self) -> None:
        """Delegate stop active turn request to active session."""
        if self.active_session:
            await self.active_session.stop_active_turn()

    async def stop_conversation(self) -> None:
        """Delegate stop conversation request to active session."""
        if self.active_session:
            self.active_session.stop()

    async def async_stop_conversation(self, force_close_socket: bool = True) -> None:
        """Delegate async stop request to active session."""
        if self.active_session:
            await self.active_session.async_stop(force_close_socket=force_close_socket)

    async def cleanup_active_session(self) -> None:
        """Safely close and clean up active session."""
        if self.active_session:
            logger.debug("[ COORDINATOR ] Cleaning up active session...")
            await self.active_session.cleanup()
            self.active_session = None

    async def cleanup(self) -> None:
        await self.cleanup_active_session()
