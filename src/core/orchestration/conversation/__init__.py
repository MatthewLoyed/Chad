"""
Conversation orchestration collaborators.

Single-responsibility classes extracted from ConversationOrchestrator to keep it
within the 1000-line ceiling defined in CLAUDE.md.

Sub-modules:
- directive_processor: Reply directive dispatch + handler registration
- activity_reporter:   Autonomous-turn dashboard feed + run-log writes
- session_lock_manager: Per-session asyncio.Lock allocation
- response_router:     Response-type routing (audio/tool_call/tool_call_batch/turn_complete/thinking)
"""

from .directive_processor import DirectiveProcessor
from .activity_reporter import AutonomousActivityReporter
from .session_lock_manager import SessionLockManager
from .response_router import TurnResponseRouter
from .concurrency_gate import TurnConcurrencyGate

__all__ = [
    "DirectiveProcessor",
    "AutonomousActivityReporter",
    "SessionLockManager",
    "TurnResponseRouter",
    "TurnConcurrencyGate",
]

