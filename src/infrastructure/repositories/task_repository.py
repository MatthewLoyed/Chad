"""
Supabase Task Repository

Data access layer for scheduled task persistence using Supabase. When Supabase
isn't configured, SupabaseManager transparently delegates to LocalPostgrestShim
(issue #465/#466) — this repository's code is identical either way; it just
needs to declare its table schemas once via `register_table`.
All methods are synchronous — SupabaseManager now uses httpx.Client (sync).
"""

import json
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.domain.models import AgentTask, TaskStatus, TaskTrigger, ToolExecution
from src.shared.logger import setup_logger
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.database import local_postgrest

logger = setup_logger("TaskRepository")

local_postgrest.register_table(
    "agent_tasks",
    columns={
        "task_id": "TEXT",
        "name": "TEXT",
        "description": "TEXT",
        "trigger": "TEXT",
        "status": "TEXT",
        "schedule": "TEXT",
        "last_run": "TEXT",
        "next_run": "TEXT",
        "metadata": "JSON",
        "created_at": "TEXT",
    },
    pk="task_id",
)
local_postgrest.register_table(
    "task_executions",
    columns={
        "id": "INTEGER",
        "task_id": "TEXT",
        "tool_name": "TEXT",
        "args": "JSON",
        "result": "JSON",
        "success": "INTEGER",
        "duration": "REAL",
        "trigger_source": "TEXT",
        "error": "TEXT",
        "timestamp": "TEXT",
    },
    pk="id",
)


class TaskRepository:
    """
    Repository for agent task persistence operations. Uses Supabase.
    All methods are synchronous (no async/await, no nest_asyncio).
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize TaskRepository.

        Args:
            db_path: Ignored for Supabase, kept for legacy compatibility.
        """
        self.supabase = get_supabase_manager()
        self.task_table = "agent_tasks"
        self.exec_table = "task_executions"

    # ─── Tasks ────────────────────────────────────────────────────────────────

    async def create_task(self, task: AgentTask) -> bool:
        """Create a new agent task."""
        data = {
            "task_id": task.task_id,
            "name": task.name,
            "description": task.description,
            "trigger": task.trigger.value,
            "status": task.status.value,
            "schedule": task.schedule,
            "last_run": task.last_run.isoformat() if task.last_run else None,
            "next_run": task.next_run.isoformat() if task.next_run else None,
            "metadata": task.metadata,
            "created_at": task.created_at.isoformat()
        }
        res = await self.supabase.async_insert(self.task_table, data)
        if res:
            # Note: executions are stored synchronously for now as they are small
            # and usually happen within a larger context.
            for execution in task.tool_executions:
                self._store_execution(task.task_id, execution)
            return True
        return False

    async def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Get a task by ID."""
        res = await self.supabase.async_query(self.task_table, filters={"task_id": f"eq.{task_id}"}, limit=1)
        if not res:
            return None
        return await self._row_to_task_async(res[0])

    async def update_task(self, task: AgentTask) -> bool:
        """Update an existing task."""
        data = {
            "name": task.name,
            "description": task.description,
            "status": task.status.value,
            "schedule": task.schedule,
            "last_run": task.last_run.isoformat() if task.last_run else None,
            "next_run": task.next_run.isoformat() if task.next_run else None,
            "metadata": task.metadata
        }
        return await self.supabase.async_update(self.task_table, filters={"task_id": f"eq.{task.task_id}"}, data=data)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task and its executions."""
        self.supabase.delete(self.exec_table, filters={"task_id": f"eq.{task_id}"})
        return self.supabase.delete(self.task_table, filters={"task_id": f"eq.{task_id}"})

    async def list_tasks(self, status: Optional[TaskStatus] = None, trigger: Optional[TaskTrigger] = None, limit: int = 100) -> List[AgentTask]:
        """List tasks with optional filtering."""
        filters = {}
        if status:
            filters["status"] = f"eq.{status.value}"
        if trigger:
            filters["trigger"] = f"eq.{trigger.value}"

        res = await self.supabase.async_query(self.task_table, filters=filters, limit=limit)
        res.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        tasks = []
        for row in res:
            tasks.append(await self._row_to_task_async(row))
        return tasks

    async def get_all_tasks(self) -> List[AgentTask]:
        return await self.list_tasks(limit=1000)

    async def get_scheduled_tasks(self) -> List[AgentTask]:
        return await self.list_tasks(status=TaskStatus.SCHEDULED)

    async def get_running_tasks(self) -> List[AgentTask]:
        return await self.list_tasks(status=TaskStatus.RUNNING)

    async def get_due_tasks(self, current_time: Optional[datetime] = None) -> List[AgentTask]:
        """Get tasks due to run."""
        if current_time is None:
            current_time = datetime.now()

        filters = {
            "status": f"eq.{TaskStatus.SCHEDULED.value}",
            "next_run": f"lte.{current_time.isoformat()}"
        }
        res = await self.supabase.async_query(self.task_table, filters=filters)
        res.sort(key=lambda x: x.get("next_run", ""))
        tasks = []
        for row in res:
            tasks.append(await self._row_to_task_async(row))
        return tasks

    # Unified Async Methods (Direct Implementation)
    async def get_due_tasks_async(self, current_time: Optional[datetime] = None) -> List[AgentTask]:
        return await self.get_due_tasks(current_time)

    async def create_task_async(self, task: AgentTask) -> bool:
        return await self.create_task(task)

    async def get_task_async(self, task_id: str) -> Optional[AgentTask]:
        return await self.get_task(task_id)

    async def update_task_async(self, task: AgentTask) -> bool:
        return await self.update_task(task)

    async def list_tasks_async(self, status: Optional[TaskStatus] = None, trigger: Optional[TaskTrigger] = None, limit: int = 100) -> List[AgentTask]:
        return await self.list_tasks(status, trigger, limit)

    async def get_all_tasks_async(self) -> List[AgentTask]:
        return await self.get_all_tasks()

    async def get_scheduled_tasks_async(self) -> List[AgentTask]:
        return await self.get_scheduled_tasks()

    async def get_running_tasks_async(self) -> List[AgentTask]:
        return await self.get_running_tasks()

    async def delete_task_async(self, task_id: str) -> bool:
        """Delete a task and its executions (async wrapper)."""
        import asyncio
        return await asyncio.to_thread(self.delete_task, task_id)

    # ─── Executions ───────────────────────────────────────────────────────────

    def _store_execution(self, task_id: str, execution: ToolExecution) -> bool:
        data = {
            "task_id": task_id,
            "tool_name": execution.tool_name,
            "args": execution.args,
            "result": execution.result,
            "success": execution.success,
            "duration": execution.duration,
            "trigger_source": execution.trigger_source.value,
            "error": execution.error,
            "timestamp": execution.timestamp.isoformat()
        }
        res = self.supabase.insert(self.exec_table, data)
        return bool(res)

    async def _load_executions_async(self, task_id: str) -> List[ToolExecution]:
        res = await self.supabase.async_query(self.exec_table, filters={"task_id": f"eq.{task_id}"})
        res.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        executions = []
        for row in res:
            executions.append(ToolExecution(
                tool_name=row['tool_name'],
                args=row['args'] or {},
                result=row['result'],
                success=bool(row['success']),
                duration=row['duration'] or 0.0,
                timestamp=datetime.fromisoformat(row['timestamp']),
                trigger_source=TaskTrigger(row['trigger_source']) if row.get('trigger_source') else TaskTrigger.USER,
                error=row.get('error')
            ))
        return executions

    async def _row_to_task_async(self, row: Dict[str, Any]) -> AgentTask:
        """Convert a database row to an AgentTask model (async)."""
        task_id = row['task_id']
        executions = await self._load_executions_async(task_id)
        return AgentTask(
            task_id=task_id,
            name=row['name'],
            description=row.get('description') or "",
            trigger=TaskTrigger(row['trigger']),
            status=TaskStatus(row['status']),
            schedule=row.get('schedule'),
            tool_executions=executions,
            created_at=datetime.fromisoformat(row['created_at']),
            last_run=datetime.fromisoformat(row['last_run']) if row.get('last_run') else None,
            next_run=datetime.fromisoformat(row['next_run']) if row.get('next_run') else None,
            metadata=row.get('metadata') or {}
        )

    # ─── Statistics ───────────────────────────────────────────────────────────

    def get_statistics(self) -> Dict[str, Any]:
        all_tasks = self.supabase.query(self.task_table, select="task_id,status")
        return {
            'total_tasks': len(all_tasks),
            'scheduled': len([t for t in all_tasks if t['status'] == TaskStatus.SCHEDULED.value]),
            'running': len([t for t in all_tasks if t['status'] == TaskStatus.RUNNING.value]),
            'completed': len([t for t in all_tasks if t['status'] == TaskStatus.COMPLETED.value]),
            'failed': len([t for t in all_tasks if t['status'] == TaskStatus.FAILED.value]),
            'remote': True
        }

    def count_tasks(self, status: Optional[TaskStatus] = None) -> int:
        stats = self.get_statistics()
        if not status:
            return stats.get('total_tasks', 0)
        return stats.get(status.value.lower(), 0)

    def cleanup_old_executions(self, days: int = 30) -> int:
        return 0  # Remote cleanup not yet implemented

    def close(self) -> None:
        pass
