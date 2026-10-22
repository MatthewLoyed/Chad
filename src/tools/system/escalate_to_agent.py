"""
Escalate to Agent — voice-mode handoff into the full agent pipeline.

Called when a voice turn needs multi-step work, non-Hubspace tools, or anything
outside the voice micro-agent's scope. TurnCoordinator detects the flag and
re-dispatches the same utterance as category=agent (14B + hybrid tool selection).
"""

from typing import Any, Dict

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("EscalateToAgentTool")


class EscalateToAgentTool(BaseTool):
    """Hand off a voice command to the full desktop agent loop."""

    name = "escalate_to_agent"
    description = (
        "Hand this request to the full agent for multi-step work, email, calendar, "
        "Canvas, files, coding, research, or any tool beyond small-talk / Hubspace. "
        "Use from voice OR Telegram contact desk. On Telegram, escalate goes to the "
        "pocket-remote telegram_agent (not desktop coding). Do NOT call for simple Q&A, "
        "greetings, or photo questions you can answer yourself. Prefer escalate over "
        "claiming you cannot access something."
    )
    category = "orchestration"
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
                        "description": "Brief reason this needs full agent mode.",
                    }
                },
                "required": ["reason"],
            },
        }

    async def execute(self, reason: str = "", **kwargs) -> Dict[str, Any]:
        logger.info(f"[ VOICE ] escalate_to_agent: {reason[:120]}")
        return {
            "success": True,
            "_voice_escalate": True,
            "reason": reason or "agent task",
            "result": "Escalating to full agent mode.",
        }
