"""
Switch to Plan Mode — routes the agent lane into the structured planner.

Does NOT submit steps. The planner later calls `propose_plan` with the actual
execution checklist.
"""

from typing import Any, Dict

from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool
from src.tools.display_helpers import format_switch_to_plan_mode

logger = setup_logger("SwitchToPlanModeTool")


class SwitchToPlanModeTool(BaseTool):
    """Enter the plan lane for complex multi-step work."""

    name = "switch_to_plan_mode"
    description = (
        "Switch into plan mode for complex, multi-step tasks that need a structured "
        "plan before execution. Use when the request needs coordination across "
        "specialists or tools. Do NOT call this when an execution plan is already "
        "active / approved — execute the existing steps instead. "
        "This does not submit plan steps; the planner will call propose_plan later."
    )
    category = "orchestration"

    def format_action(self, params: Dict[str, Any]) -> str:
        return format_switch_to_plan_mode(params)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Brief explanation of why plan mode is needed.",
                    }
                },
                "required": ["reasoning"],
            },
        }

    async def execute(self, reasoning: str, **kwargs) -> Dict[str, Any]:
        logger.info("[ SWITCH_TO_PLAN_MODE ] Triggered with reasoning: %s", reasoning)
        return {
            "success": True,
            "result": (
                f"Switching to plan mode. Reason: {reasoning}. "
                "Transitioning to the planner…"
            ),
            "_trigger_planner": True,
        }
