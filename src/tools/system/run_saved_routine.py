"""
run_saved_routine — execute a user-saved routine (Supabase workflows table) by name.

Distinct from run_routine (profile YAML app-launch macros). Chat can invoke saved
routines with phrases like "run my morning briefing".
"""

from typing import Any, Dict, List, Optional

from src.tools.base_tool import BaseTool
from src.infrastructure.repositories.workflow_repository import WorkflowRepository
from src.core.routines.workflow_executor import execute_workflow_steps
from src.shared.logger import setup_logger

logger = setup_logger("RunSavedRoutineTool")


class RunSavedRoutineTool(BaseTool):
    name = "run_saved_routine"
    description = (
        "Run a saved routine (reusable multi-step sequence in the Routines workspace) "
        "when the user asks to run one by name — distinct from run_routine "
        "(OS app-launch macros). Pass the exact routine name (case-insensitive)."
    )
    category = "system"
    is_side_effecting = True

    def __init__(self, config=None, registry=None):
        super().__init__(config)
        self._registry = registry

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "routine_name": {
                        "type": "string",
                        "description": "Name of the saved routine to run (as shown in the Routines workspace).",
                    }
                },
                "required": ["routine_name"],
            },
        }

    async def _find_routine(self, name: str) -> Optional[Dict[str, Any]]:
        repo = WorkflowRepository()
        workflows = await repo.list_workflows()
        key = name.lower().strip()
        for wf in workflows:
            if (wf.get("name") or "").lower().strip() == key:
                return wf
        return None

    async def execute(self, routine_name: str, **kwargs) -> Dict[str, Any]:
        if not routine_name or not routine_name.strip():
            return {"success": False, "error": "routine_name is required."}
        if not self._registry:
            return {"success": False, "error": "Tool registry not available."}

        wf = await self._find_routine(routine_name)
        if not wf:
            repo = WorkflowRepository()
            names = [w.get("name") for w in await repo.list_workflows() if w.get("name")]
            hint = f" Available: {', '.join(names[:12])}" if names else ""
            return {
                "success": False,
                "error": f"Routine '{routine_name}' not found.{hint}",
            }

        steps = wf.get("steps") or []
        if not steps:
            return {"success": False, "error": f"Routine '{wf.get('name')}' has no steps."}

        logger.info(f"[ ROUTINE ] Running saved routine '{wf.get('name')}' ({len(steps)} steps)")
        lines, ok = await execute_workflow_steps(
            self._registry,
            steps,
            event_bus=None,
            correlation_prefix=f"saved-routine:{wf.get('id', 'unknown')}",
        )
        summary = "\n".join(lines)
        return {
            "success": ok,
            "result": summary,
            "routine": wf.get("name"),
            "steps_run": len(lines),
        }
