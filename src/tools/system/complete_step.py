"""
Complete Step Tool - Explicitly marks the active plan step as done.
Replaces the fragile [[STEP_COMPLETE]] sentinel with a proper tool call.
"""

from typing import Dict, Any
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("CompleteStepTool")


class CompleteStepTool(BaseTool):
    """
    Call this when the active plan step has been fully accomplished.
    Provide the step_id and a brief summary of what was done.
    The orchestrator will advance the plan to the next step automatically.
    """

    name = "complete_step"
    description = (
        "Marks the currently active plan step as successfully completed. "
        "Call this immediately after the step's goal is achieved. "
        "The orchestrator will advance to the next step automatically."
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
                    "step_id": {
                        "type": "string",
                        "description": "The ID of the completed step (e.g. 'step_1')."
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-sentence summary of what was accomplished."
                    }
                },
                "required": ["step_id", "summary"]
            }
        }

    async def execute(self, step_id: str, summary: str = "", **kwargs) -> Dict[str, Any]:
        logger.info(f"[ COMPLETE_STEP ] Step '{step_id}' complete. Summary: {summary}")
        return {
            "success": True,
            "_step_complete": True,
            "step_id": step_id,
            "summary": summary,
            "result": f"Step '{step_id}' marked as completed."
        }
