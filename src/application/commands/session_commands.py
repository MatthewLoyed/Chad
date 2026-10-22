"""
Session-related commands
"""

from dataclasses import dataclass
from typing import Any, Optional, Dict
from .base_command import Command


@dataclass
class GetSessionStateCommand(Command):
    """Command to get current session state."""

    async def execute(self, facade) -> Dict[str, Any]:
        """Execute: get session state."""
        return {
            "is_active": facade.is_session_active(),
            "state": facade.get_session_state()
        }

    def __str__(self) -> str:
        return "GetSessionStateCommand()"


@dataclass
class GetVoiceNameCommand(Command):
    """Command to get configured voice name."""

    async def execute(self, facade) -> str:
        """Execute: get voice name."""
        return facade.get_voice_name()

    def __str__(self) -> str:
        return "GetVoiceNameCommand()"
