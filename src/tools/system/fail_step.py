"""
Fail Step Tool - Explicitly marks the active plan step as failed.
Replaces the fragile [[STEP_FAILED]] sentinel with a proper tool call.
"""

from typing import Dict, Any
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("FailStepTool")


class FailStepTool(BaseTool):
    """
    Call this when the active plan step is blocked under the current approach.
    Provide a clear reason so the supervisor can retry the SAME step differently
    (not wipe the plan / scout from scratch).
    """

    name = "fail_step"
    description = (
        "Signals that the current approach for this plan step is not working. "
        "Call when you have exhausted the current strategy. Provide a clear reason "
        "(what you tried and why it failed). The supervisor keeps the plan and retries "
        "THIS step with a different approach — it does NOT re-scout or create a new plan."
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
                        "description": "The ID of the failed step (e.g. 'step_2')."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Clear explanation of why the step failed and what was tried."
                    }
                },
                "required": ["step_id", "reason"]
            }
        }

    async def execute(self, step_id: str, reason: str, **kwargs) -> Dict[str, Any]:
        logger.warning(f"[ FAIL_STEP ] Step '{step_id}' failed. Reason: {reason}")
        return {
            "success": True,
            "_step_failed": True,
            "step_id": step_id,
            "reason": reason,
            "result": f"Step '{step_id}' marked as failed: {reason}"
        }
