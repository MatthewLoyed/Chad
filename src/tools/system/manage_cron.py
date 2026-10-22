"""
Manage Cron Tool - Configure scheduled tasks

Allows Chad to add, remove, and list cron tasks through natural conversation.
"""

from typing import Dict, Any, Optional
from datetime import datetime
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.domain.models import TaskTrigger

logger = setup_logger("ManageCronTool")

class ManageCronTool(BaseTool):
    """Tool for managing scheduled cron tasks."""

    name = "manage_cron"
    description = "Manage autonomous background tasks. Use for reminders, scheduled actions, or periodic checks."
    category = "system"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._server = None

    def _get_server(self):
        """Lazy-load server instance."""
        if self._server is None:
            from src.remote.server import ChadServer
            self._server = getattr(ChadServer, 'instance', None)
        return self._server

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "remove", "list"],
                        "description": "Action: add, remove, or list."
                    },
                    "task_name": {
                        "type": "string",
                        "description": "Unique task name."
                    },
                    "task_description": {
                        "type": "string",
                        "description": "Detailed instructions for the task."
                    },
                    "schedule": {
                        "type": "string",
                        "description": "Schedule (cron '0 9 * * *', ISO '2026-03-20T15:00:00', or interval '5m', '2h')."
                    },
                    "trigger_type": {
                        "type": "string",
                        "enum": ["cron", "at", "every"],
                        "description": "Type: 'cron', 'at', or 'every'."
                    },
                    "target_platform": {
                        "type": "string",
                        "enum": ["telegram", "pc", "auto"],
                        "description": "Notification destination: 'telegram', 'pc', or 'auto'."
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID for removal."
                    }
                },
                "required": ["action"]
            }
        }

    async def execute(self, action: str, context: Optional['RequestContext'] = None, **kwargs) -> Dict[str, Any]:
        try:
            server = self._get_server()
            if not server or not server._cron_scheduler:
                return {
                    "success": False,
                    "error": "Server instance or cron scheduler not found. Cannot manage cron tasks in this environment."
                }

            scheduler = server._cron_scheduler

            if action == "add":
                name = kwargs.get("task_name")
                description = kwargs.get("task_description")
                schedule = kwargs.get("schedule")
                trigger_str = kwargs.get("trigger_type")
                target_platform = kwargs.get("target_platform", "auto")

                if not all([name, description, schedule, trigger_str]):
                    return {"success": False, "error": "Missing required fields for add action: task_name, task_description, schedule, trigger_type"}

                # Map string trigger to enum
                trigger_map = {
                    "cron": TaskTrigger.CRON,
                    "at": TaskTrigger.AT,
                    "every": TaskTrigger.EVERY
                }
                trigger = trigger_map.get(trigger_str.lower())
                if not trigger:
                    return {"success": False, "error": f"Invalid trigger_type: {trigger_str}"}

                session_key = context.session_id if context else kwargs.get("session_key")
                platform_key = context.platform if context else "unknown"

                metadata = {
                    "session_key": session_key, 
                    "platform": platform_key,
                    "target_platform": target_platform
                }

                logger.info(f"Adding cron task: '{name}' with schedule: '{schedule}' (trigger: {trigger})")
                
                # Await the async add_task
                task_id = await scheduler.add_task(
                    name=name,
                    description=description,
                    schedule=schedule,
                    trigger=trigger,
                    metadata=metadata
                )
                
                return {
                    "success": True, 
                    "result": f"Successfully created scheduled task '{name}'. ID: {task_id}",
                    "task_id": task_id
                }

            elif action == "remove":
                task_id = kwargs.get("task_id")
                if not task_id:
                    return {"success": False, "error": "task_id is required for remove action"}

                # Await the async remove_task
                success = await scheduler.remove_task(task_id)
                if success:
                    return {"success": True, "result": f"Removed task ID: {task_id}"}
                else:
                    return {"success": False, "error": f"Task ID {task_id} not found."}

            elif action == "list":
                # Await the async list_tasks
                tasks = await scheduler.list_tasks()
                if not tasks:
                    return {"success": True, "result": "No tasks currently scheduled."}

                res = "Scheduled tasks:\\n"
                for t in tasks:
                    next_run_str = t.next_run.strftime('%Y-%m-%d %H:%M:%S') if t.next_run else "N/A"
                    res += f"- Name: {t.name}\\n  ID: {t.task_id}\\n  Trigger: {t.trigger.value}\\n  Schedule: {t.schedule}\\n  Next Run: {next_run_str}\\n\\n"
                
                return {"success": True, "result": res}

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            logger.error(f"Cron manager action '{action}' failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
