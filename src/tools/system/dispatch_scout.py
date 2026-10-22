"""
Dispatch Scout — planner-phase tool to send a gather-only brief to Scout.

The planner calls this when it needs environment facts (paths, inbox state, file
contents, etc.) before it can write an accurate execution plan. Scout runs
read-only and returns a gather_brief; clarifying questions stay on
ask_user_question and must never be execution-plan steps.
"""

from typing import Any, Dict, Optional

from src.tools.base_tool import BaseTool


class DispatchScoutTool(BaseTool):
    """Hand a concrete gather brief to the Scout limb (read-only recon)."""

    name = "dispatch_scout"
    description = (
        "Give Scout a concrete read-only brief to gather missing facts needed to execute "
        "the task: web research for unfamiliar terms/entities, memory/contacts for "
        "email addresses, and verification of relevant files, installed apps or browser "
        "state. Request sources and unresolved gaps. Do not request unrelated inventories, "
        "plans or task execution. The planner must dispatch Scout or explicitly skip it "
        "before proposing a plan."
    )
    category = "delegation"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "brief": {
                        "type": "string",
                        "description": (
                            "Concrete gather instructions for Scout: what facts to find "
                            "and what to report back (paths, summaries, gaps)."
                        ),
                    },
                },
                "required": ["brief"],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        brief = (params.get("brief") or "").strip()
        if brief:
            short = brief if len(brief) <= 80 else brief[:77] + "..."
            return f"Dispatching Scout: {short}"
        return "Dispatching Scout"

    async def execute(self, brief: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        text = (brief or "").strip()
        if not text:
            return {
                "success": False,
                "error": "dispatch_scout requires a non-empty `brief`.",
            }
        return {
            "success": True,
            "_dispatch_scout": True,
            "brief": text,
            "result": f"Scout dispatched with gather brief ({len(text)} chars).",
        }
