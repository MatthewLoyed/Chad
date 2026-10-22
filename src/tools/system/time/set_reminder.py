"""
Set Reminder Tool - Passive human-readable alerts
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

logger = setup_logger("SetReminderTool")


class SetReminderTool(BaseTool):
    """Tool for scheduling passive persistent reminders (notifications only)."""

    name = "set_reminder"
    description = """Use this ONLY for future notifications or alerts. 
This tool CANNOT perform actions or call other tools.
MANDATORY: If a user specifies a time in the future (e.g., 'in 2 mins', 'tomorrow') and just wants a notification, use this."""
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
                        "description": "Action to perform: 'set' a reminder, 'list' active reminders, or 'cancel' a reminder."
                    },
                    "when": {
                        "type": "string",
                        "description": "When to trigger the reminder. e.g., 'in 1 minute', 'every 1d', '2024-03-22T10:00:00'."
                    },
                    "message": {
                        "type": "string",
                        "description": "What to remind the user about (e.g., 'check the oven')."
                    },
                    "reminder_id": {
                        "type": "string",
                        "description": "ID of reminder to cancel. Required for 'cancel' action."
                    }
                },
                "required": ["action"]
            }
        }

    async def execute(
        self,
        action: str,
        when: Optional[str] = None,
        message: Optional[str] = None,
        reminder_id: Optional[str] = None,
        context: Optional['RequestContext'] = None,
        **kwargs
    ) -> Dict[str, Any]:
        try:
            if action == "set":
                if not when:
                    return {"success": False, "error": "Time ('when') is required to set a reminder."}
                if not message:
                    return {"success": False, "error": "Message is required for a reminder."}
                
                session_key = context.session_id if context else kwargs.get("session_key") or "global"
                platform_key = context.platform if context else "unknown"
                user_id = context.user_id if context else "unknown"
                
                return await self._set_reminder(when, message, session_key, platform_key, user_id)

            elif action == "list":
                return await self._list_reminders()

            elif action == "cancel":
                if not reminder_id:
                    return {"success": False, "error": "reminder_id is required to cancel."}
                return await self._cancel_reminder(reminder_id)

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            logger.error(f"SetReminder action '{action}' failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _set_reminder(self, when: str, message: str, session_key: str, platform_key: str, user_id: str) -> Dict[str, Any]:
        """Set a passive reminder."""
        try:
            target_time, trigger, schedule = self._parse_time(when)
            if not target_time:
                return {"success": False, "error": f"Could not parse time: '{when}'."}
            if trigger == TaskTrigger.AT and target_time < datetime.now():
                return {"success": False, "error": "Cannot set reminder for the past."}

            task_id = str(uuid.uuid4())
            task = AgentTask(
                task_id=task_id,
                name=f"reminder_{message[:30]}",
                description=message,
                trigger=trigger,
                schedule=schedule,
                status=TaskStatus.SCHEDULED,
                next_run=target_time,
                metadata={
                    "type": "reminder",
                    "is_agentic": False, # Passively alert user
                    "message": message,
                    "created_by": "user",
                    "created_at": datetime.now().isoformat(),
                    "session_key": session_key,
                    "platform": platform_key,
                    "user_id": user_id,
                    "session_type": "main"
                }
            )
            
            repo = self._get_task_repo()
            if await repo.create_task(task):
                time_display = self._format_time_display(target_time, trigger)
                return {
                    "success": True,
                    "result": f"Reminder set for {time_display}: '{message}'",
                    "reminder_id": task_id
                }
            return {"success": False, "error": "Failed to save reminder."}
        except Exception as e:
            logger.error(f"Failed to set reminder: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _list_reminders(self) -> Dict[str, Any]:
        """List active passive reminders only (State Isolation)."""
        try:
            repo = self._get_task_repo()
            all_tasks = await repo.get_scheduled_tasks()
            
            # State Isolation: Filter for is_agentic: False
            reminders = [
                t for t in all_tasks 
                if t.metadata.get("type") == "reminder" 
                and not t.metadata.get("is_agentic", False)
                and t.status == TaskStatus.SCHEDULED
            ]
            
            if not reminders:
                return {"success": True, "result": "No active reminders."}

            reminder_list = []
            for r in reminders:
                reminder_list.append({
                    "id": r.task_id,
                    "time": r.next_run.strftime("%Y-%m-%d %H:%M"),
                    "message": r.metadata.get("message", r.description)
                })

            result_text = "Active reminders:\n" + "\n".join([f"- [{r['id']}] {r['time']}: {r['message']}" for r in reminder_list])
            return {"success": True, "result": result_text, "reminders": reminder_list}
        except Exception as e:
            logger.error(f"Failed to list reminders: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cancel_reminder(self, reminder_id: str) -> Dict[str, Any]:
        """Cancel a passive reminder (State Isolation)."""
        try:
            repo = self._get_task_repo()
            task = await repo.get_task(reminder_id)
            if not task:
                return {"success": False, "error": f"Reminder not found: {reminder_id}"}
                
            # State Isolation Safety Check
            if task.metadata.get("is_agentic", False):
                return {
                    "success": False, 
                    "error": "This task is an autonomous command. Please use 'execute_future_task' to manage it."
                }

            if repo.delete_task(reminder_id):
                return {"success": True, "result": f"Cancelled reminder: '{task.metadata.get('message', 'unknown')}'"}
            return {"success": False, "error": "Failed to delete reminder."}
        except Exception as e:
            logger.error(f"Failed to cancel reminder: {e}", exc_info=True)
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
