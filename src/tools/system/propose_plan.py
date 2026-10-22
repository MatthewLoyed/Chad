"""
Propose Plan — planner-phase tool that submits the execution plan.

The planner calls this when info-needs are done. Steps land in tool args
(tool_calls channel), which is reliable on local models that otherwise put
JSON schema output in the reasoning stream.
"""

from typing import Any, Dict, List, Optional

from src.tools.base_tool import BaseTool


class ProposePlanTool(BaseTool):
    """Submit a concrete multi-step execution plan for user approval."""

    name = "propose_plan"
    description = (
        "Submit the execution plan when you have enough information. "
        "Pass concrete DO steps the supervisor will run after the user approves. "
        "Do NOT put ask_user_question, pause_for_input, or dispatch_scout on steps. "
        "Call this instead of writing JSON in your message."
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
                    "steps": {
                        "type": "array",
                        "description": (
                            "Ordered execution steps. Each step needs a description and "
                            "a tools array (tool names from the confirmed menu, or [])."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Optional step id (e.g. step_1).",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "One concrete, verifiable DO action.",
                                },
                                "tools": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Tool names from the confirmed menu for this step, "
                                        "or [] for a pure present/summarize step."
                                    ),
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending"],
                                    "description": "New steps start pending; execution owns status changes.",
                                },
                            },
                            "required": ["description", "tools"],
                        },
                    },
                },
                "required": ["steps"],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        steps = params.get("steps") or []
        n = len(steps) if isinstance(steps, list) else 0
        return f"Proposing {n}-step plan" if n else "Proposing plan"

    async def execute(self, steps: Optional[List[Dict[str, Any]]] = None, **kwargs: Any) -> Dict[str, Any]:
        raw = [dict(step) for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
        if not raw:
            return {
                "success": False,
                "error": "propose_plan requires a non-empty `steps` array.",
            }
        return {
            "success": True,
            "_propose_plan": True,
            "steps": [{**step, "status": "pending"} for step in raw if isinstance(step, dict)],
            "result": f"Plan proposed with {len(raw)} step(s).",
        }
