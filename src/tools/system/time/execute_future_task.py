"""
Execute Future Task Tool - Active autonomous delegation (Agentic Cron)
Part of the specialized scheduling system to resolve Tool Confusion.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import re
import uuid
import logging

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.domain.models import AgentTask, TaskTrigger, TaskStatus

logger = setup_logger("ExecuteFutureTaskTool")


class ExecuteFutureTaskTool(BaseTool):
    """Tool for scheduling autonomous agentic commands."""

    name = "execute_future_task"
    description = """Use this to delegate an action to your future self. 
Use this if the user says 'In 10 minutes, do [X]'. 
This tool schedules a command that I will execute autonomously at the trigger time."""
    category = "system"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._task_repo = None

    def _get_task_repo(self):
        """Lazy-load task repository."""
        if self._task_repo is None:
            from src.infrastructure.repositories.task_repository import TaskRepository
            from src.shared.config_manager import ConfigManager
            config = ConfigManager()
            db_path = config.get("task_db_path", "data/tasks.db")
            self._task_repo = TaskRepository(db_path)
        return self._task_repo

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set", "list", "cancel"],
                        "description": "Action to perform: 'set' a future task, 'list' active tasks, or 'cancel' a task."
                    },
                    "when": {
                        "type": "string",
                        "description": "When to trigger the command. e.g., 'in 10 minutes', 'every 2h', '2024-03-22T15:00:00'."
                    },
                    "command": {
                        "type": "string",
                        "description": "The autonomous command to execute (e.g., 'research the web for clash royale decks')."
                    },
                    "reminder_id": {
                        "type": "string",
                        "description": "ID of task to cancel. Required for 'cancel' action."
                    }
                },
                "required": ["action"]
            }
        }

    async def execute(
        self,
        action: str,
        when: Optional[str] = None,
        command: Optional[str] = None,
        reminder_id: Optional[str] = None,
        context: Optional['RequestContext'] = None,
        **kwargs
    ) -> Dict[str, Any]:
        try:
            if action == "set":
                if not when:
                    return {"success": False, "error": "Time ('when') is required to schedule a future task."}
                if not command:
                    return {"success": False, "error": "Command is required for the future task."}
                
                session_key = context.session_id if context else kwargs.get("session_key") or "global"
                platform_key = context.platform if context else "unknown"
                user_id = context.user_id if context else "unknown"
                
                return await self._set_future_task(when, command, session_key, platform_key, user_id)

            elif action == "list":
                return await self._list_future_tasks()

            elif action == "cancel":
                if not reminder_id:
                    return {"success": False, "error": "reminder_id is required to cancel."}
                return await self._cancel_future_task(reminder_id)

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            logger.error(f"ExecuteFutureTask action '{action}' failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _set_future_task(self, when: str, command: str, session_key: str, platform_key: str, user_id: str) -> Dict[str, Any]:
        """Schedule an autonomous command."""
        try:
            target_time, trigger, schedule = self._parse_time(when)
            if not target_time:
                return {"success": False, "error": f"Could not parse time: '{when}'."}
            
            # Create task
            task_id = str(uuid.uuid4())
            
            # Ensure "Reminder:" prefix is stripped for agentic loop
            clean_command = re.sub(r'^Reminder:\s*', '', command, flags=re.IGNORECASE)
            
            task = AgentTask(
                task_id=task_id,
                name=f"agentic_{clean_command[:30]}",
                description=clean_command,
                trigger=trigger,
                schedule=schedule,
                status=TaskStatus.SCHEDULED,
                next_run=target_time,
                metadata={
                    "type": "reminder", # Keep type as reminder if server.py expects it
                    "is_agentic": True,
                    "command": clean_command,
                    "created_by": "user",
                    "created_at": datetime.now().isoformat(),
                    "session_key": session_key,
                    "platform": platform_key,
                    "user_id": user_id,
                    "session_type": "main"
                }
            )
            
            repo = self._get_task_repo()
            if repo.create_task(task):
                time_display = self._format_time_display(target_time, trigger)
                return {
                    "success": True,
                    "result": f"Autonomous task scheduled for {time_display}: '{clean_command}'",
                    "reminder_id": task_id
                }
            return {"success": False, "error": "Failed to save task to database."}
        except Exception as e:
            logger.error(f"Failed to schedule future task: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _list_future_tasks(self) -> Dict[str, Any]:
        """List active autonomous tasks only (State Isolation)."""
        try:
            repo = self._get_task_repo()
            all_tasks = repo.get_scheduled_tasks()
            
            # State Isolation: Filter for is_agentic: True
            tasks = [
                t for t in all_tasks 
                if t.metadata.get("is_agentic", False)
                and t.status == TaskStatus.SCHEDULED
            ]
            
            if not tasks:
                return {"success": True, "result": "No active autonomous tasks."}

            task_list = []
            for t in tasks:
                task_list.append({
                    "id": t.task_id,
                    "time": t.next_run.strftime("%Y-%m-%d %H:%M"),
                    "command": t.description
                })

            result_text = "Active autonomous tasks:\n" + "\n".join([f"- [{t['id']}] {t['time']}: {t['command']}" for t in task_list])
            return {"success": True, "result": result_text, "tasks": task_list}
        except Exception as e:
            logger.error(f"Failed to list future tasks: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cancel_future_task(self, reminder_id: str) -> Dict[str, Any]:
        """Cancel an autonomous task (State Isolation)."""
        try:
            repo = self._get_task_repo()
            task = repo.get_task(reminder_id)
            if not task:
                return {"success": False, "error": f"Task not found: {reminder_id}"}
                
            # State Isolation Safety Check
            if not task.metadata.get("is_agentic", False):
                return {
                    "success": False, 
                    "error": "This task is a simple reminder. Please use 'set_reminder' to manage it."
                }

            if repo.delete_task(reminder_id):
                return {"success": True, "result": f"Cancelled autonomous task: '{task.description}'"}
            return {"success": False, "error": "Failed to delete task."}
        except Exception as e:
            logger.error(f"Failed to cancel future task: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _parse_time(self, when: str) -> tuple[Optional[datetime], TaskTrigger, str]:
        when = when.strip().lower()
        relative_match = re.match(r'^in\s+(\d+)\s*(second|minute|hour|day|week)s?$', when)
        if relative_match:
            value = int(relative_match.group(1))
            unit = relative_match.group(2)
            delta_map = {
                'second': timedelta(seconds=value), 'minute': timedelta(minutes=value),
                'hour': timedelta(hours=value), 'day': timedelta(days=value), 'week': timedelta(weeks=value)
            }
            target_time = datetime.now() + delta_map[unit]
            return target_time, TaskTrigger.AT, target_time.isoformat()

        every_match = re.match(r'^every\s+(\d+)\s*(m|h|d)$', when)
        if every_match:
            value = every_match.group(1)
            unit = every_match.group(2)
            delta_map = {'m': timedelta(minutes=int(value)), 'h': timedelta(hours=int(value)), 'd': timedelta(days=int(value))}
            target_time = datetime.now() + delta_map[unit]
            return target_time, TaskTrigger.EVERY, f"{value}{unit}"

        try:
            from dateutil import parser as dateutil_parser
            target_time = dateutil_parser.parse(when)
            return target_time, TaskTrigger.AT, target_time.isoformat()
        except:
            return None, TaskTrigger.AT, when

    def _format_time_display(self, target_time: datetime, trigger: TaskTrigger) -> str:
        if trigger == TaskTrigger.EVERY:
            return f"recurring starting at {target_time.strftime('%I:%M %p')}"
        now = datetime.now()
        diff = target_time - now
        if diff.total_seconds() < 3600:
            minutes = int(diff.total_seconds() / 60)
            return f"in {minutes} minute{'s' if minutes != 1 else ''}"
        time_str = target_time.strftime("%I:%M %p")
        if target_time.date() > now.date():
            time_str += f" on {target_time.strftime('%B %d')}"
        return time_str
