"""
Stay Silent Tool - 2026 Proactive Agent Silence Action Gate.

Enables an autonomous agent to explicitly choose silence rather than hallucinating
urgency or attempting to generate text to explain its restraint.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display


@tool_display("Keeping this check silent")
class StaySilentTool(BaseTool):
    """Action gate allowing autonomous agents to remain silent without generating text."""

    name = "stay_silent"
    description = (
        "ACTION: Choose to stay completely silent. Call this tool when a background check, "
        "email event, or autonomous cycle requires NO interruption or outreach to the user. "
        "Calling this tool guarantees zero notification is sent."
    )
    category = "custom"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Optional internal reason for silence (e.g. 'no urgent emails', 'spam only', 'stale history').",
                    }
                },
                "required": [],
            },
        }

    async def execute(self, reason: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        return {
            "success": True,
            "stay_silent": True,
            "reason": reason or "autonomous_silence_chosen",
        }
