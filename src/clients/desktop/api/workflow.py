import asyncio
from src.application.commands import (
    ListWorkflowsCommand,
    CreateWorkflowCommand,
    UpdateWorkflowCommand,
    DeleteWorkflowCommand,
    ListToolCatalogCommand,
    DraftRoutineCommand,
)
from src.shared.logger import setup_logger

logger = setup_logger("WorkflowAPIMixin")


class WorkflowAPIMixin:
    """Mixin for saved routines (workflows) WebView API methods."""

    def get_workflows(self):
        """Get all saved routines for UI display."""
        return self._execute_command(ListWorkflowsCommand(), timeout=5.0, default_fallback=[])

    def get_routines(self):
        """Alias for get_workflows."""
        return self.get_workflows()

    def list_tool_catalog(self):
        """Tool names, descriptions, and schemas for the routine builder UI."""
        return self._execute_command(ListToolCatalogCommand(), timeout=15.0, default_fallback=[])

    def draft_routine(self, description):
        """Draft routine steps from natural language using the supervisor model."""
        if not description or not str(description).strip():
            return {"success": False, "steps": [], "error": "Description is required."}
        return self._execute_command(
            DraftRoutineCommand(description=str(description).strip()),
            timeout=120.0,
            default_fallback={"success": False, "steps": [], "error": "Server unreachable"},
        )

    def create_workflow(self, name, steps, description=None):
        """Create a new workflow. steps: list of {tool_name, params, label}."""
        if not name or not name.strip():
            return {"success": False, "error": "Name cannot be empty"}
        workflow = self._execute_command(
            CreateWorkflowCommand(name=name.strip(), steps=steps or [], description=description),
            timeout=5.0, default_fallback=None,
        )
        return {"success": workflow is not None, "workflow": workflow}

    def update_workflow(self, workflow_id, name=None, description=None, steps=None):
        """Update a workflow's name/description/steps."""
        success = self._execute_command(
            UpdateWorkflowCommand(workflow_id=workflow_id, name=name, description=description, steps=steps),
            timeout=5.0, default_fallback=False,
        )
        return {"success": success}

    def delete_workflow(self, workflow_id):
        """Delete a workflow."""
        success = self._execute_command(DeleteWorkflowCommand(workflow_id=workflow_id), timeout=5.0, default_fallback=False)
        return {"success": success}

    def run_workflow(self, workflow_id):
        """Trigger a saved workflow — runs each step's tool directly, skipping the LLM/planner.
        See LimbMessageHandler._handle_workflow_run on the Brain side."""
        window = self.main_window_ref()
        if not window or not workflow_id:
            return False

        try:
            app = window.app_reference
            if not app or not app.is_initialized:
                return False

            if hasattr(app, 'uplink_limb') and app.uplink_limb:
                async def _dispatch():
                    try:
                        await app.uplink_limb.send_workflow_run(workflow_id)
                    except Exception as send_err:
                        logger.error(f"[ BRIDGE ] : Failed to transmit workflow run: {send_err}")

                if app.loop and not app.loop.is_closed():
                    asyncio.run_coroutine_threadsafe(_dispatch(), app.loop)
                    return True

            return False
        except Exception as e:
            logger.error(f"[ BRIDGE ] : Error dispatching workflow run: {e}", exc_info=True)
            return False
