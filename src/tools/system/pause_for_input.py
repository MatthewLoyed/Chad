"""
Pause For Input Tool - Explicitly pauses plan execution to wait for user input.
Replaces the fragile [[PAUSE]] sentinel with a proper tool call.
"""

from typing import Dict, Any
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("PauseForInputTool")


class PauseForInputTool(BaseTool):
    """
    Call this when the plan cannot continue without a decision or information from the user.
    Execution will pause and the user will be shown your message before the plan resumes.
    """

    name = "pause_for_input"
    description = (
        "Pauses plan execution and surfaces a question or decision to the user. "
        "Call this when you need user input before the next step can proceed. "
        "Execution resumes on the user's next message."
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
                    "message": {
                        "type": "string",
                        "description": "The question or information you need from the user before continuing."
                    }
                },
                "required": ["message"]
            }
        }

    async def execute(self, message: str, **kwargs) -> Dict[str, Any]:
        logger.info(f"[ PAUSE_FOR_INPUT ] Pausing plan. Message: {message}")
        return {
            "success": True,
            "_pause_for_input": True,
            "message": message,
            "result": message
        }
