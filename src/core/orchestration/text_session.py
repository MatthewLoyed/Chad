"""
Text Session - Manages the lifecycle of turn-based text interaction.
Extracted from SessionManager.
"""

import logging
from typing import Dict, Any, Optional
from src.application.events import EventBus
from src.shared.logger import setup_logger

logger = setup_logger("TextSession")
logger.setLevel(logging.WARNING)

class TextSession:
    """
    Manages standard turn-based text chat sessions.
    Delegates generation, database logging, and lifecycle events directly to the ConversationOrchestrator.
    """
    def __init__(
        self,
        conversation_orchestrator: Any,
        event_bus: EventBus
    ):
        self.orchestrator = conversation_orchestrator
        self.event_bus = event_bus
        self.is_active = False

    async def start(self) -> None:
        self.is_active = True
        logger.info("[ TEXT SESSION ] Text session started.")

    async def execute_command(self, command: str, category: str = 'chat') -> Dict[str, Any]:
        """
        Execute a standard turn-based text command/request.
        """
        if not self.is_active:
            await self.start()

        logger.info(f"[ TEXT SESSION ] Executing request: '{command[:50]}'")
        
        # Delegates directly to the high-level ConversationOrchestrator processing loop
        response_text = await self.orchestrator.process_request(
            prompt=command,
            category=category
        )
        
        return {
            "success": True,
            "response": response_text
        }

    def stop(self) -> None:
        self.is_active = False
        logger.info("[ TEXT SESSION ] Text session stopped.")

    async def stop_active_turn(self) -> None:
        """Interrupt the currently active turn in the orchestrator."""
        logger.warning("[ TEXT SESSION ] Interrupting active text turn...")
        await self.orchestrator.cancel_turn(reason="user_requested")

    async def cleanup(self) -> None:
        self.stop()
