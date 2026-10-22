"""
Orchestration Layer - Coordinates conversation flow and lifecycle management
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conversation_orchestrator import ConversationOrchestrator, TurnContext, TurnInitiator

from .state_coordinator import StateCoordinator
from .session_manager import SessionManager

__all__ = [
    'StateCoordinator',
    'SessionManager'
]
