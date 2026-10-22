"""
Automation Commands

Backs the "Autonomous Actions" settings panel: what wakes Chad (heartbeat,
cron, webhooks) and lets the user see/configure it. Mirrors the dashboard
and kanban command modules — thin dataclasses that call straight into the
existing repositories/services, no new transport.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Optional, Dict, List, Any

from src.application.commands.base_command import Command
from src.domain.models import AgentTask, TaskStatus, TaskTrigger

VALID_CRON_TRIGGERS = {"cron": TaskTrigger.CRON, "at": TaskTrigger.AT, "every": TaskTrigger.EVERY}


def _task_summary(task: AgentTask) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "name": task.name,
        "description": task.description,
        "trigger": task.trigger.value,
        "status": task.status.value,
        "schedule": task.schedule,
        "last_run": task.last_run.isoformat() if task.last_run else None,
        "next_run": task.next_run.isoformat() if task.next_run else None,
        "is_agentic": bool(task.metadata.get("is_agentic", False)),
        "execution_count": len(task.tool_executions),
    }


@dataclass
class GetAutomationsSummaryCommand(Command):
    """What wakes Chad: daemons, heartbeat checklist, cron count, webhooks, recent runs."""

    async def execute(self, facade) -> Dict[str, Any]:
        from src.infrastructure.repositories.memory_repository import MemoryRepository
        from src.infrastructure.repositories.task_repository import TaskRepository
        from src.remote.webhooks.webhook_manager import get_webhook_manager
        from src.core.monitoring.daemon_status import get_daemon_snapshot, daemons_master_enabled
        from src.core.monitoring.autonomous_run_log import list_runs

        heartbeat_service = getattr(facade, "heartbeat_service", None)
        heartbeat_status = heartbeat_service.get_status() if heartbeat_service else {
            "status": "offline", "interval_minutes": 30, "last_check": None
        }
        checklist = MemoryRepository().get_persona_config("heartbeat") or ""

        tasks = await TaskRepository().get_all_tasks_async()
        scheduled = [t for t in tasks if t.status == TaskStatus.SCHEDULED]

        webhook_status = get_webhook_manager().get_status()

        return {
            "autonomous_actions_enabled": daemons_master_enabled(),
            "autonomous_actions_locked": bool(os.getenv("CHAD_DAEMONS_ENABLED", "")),
            "heartbeat": {**heartbeat_status, "checklist": checklist},
            "cron": {"task_count": len(scheduled), "total_count": len(tasks)},
            "webhooks": webhook_status,
            "daemons": get_daemon_snapshot(facade),
            "recent_runs": list_runs(limit=30),
        }


@dataclass
class ListAutonomousRunsCommand(Command):
    """Recent autonomous activity log (newest first)."""
    limit: int = 50
    source: Optional[str] = None

    async def execute(self, facade) -> List[Dict[str, Any]]:
        from src.core.monitoring.autonomous_run_log import list_runs
        return list_runs(limit=self.limit, source=self.source)


@dataclass
class UpdateDaemonSettingsCommand(Command):
    """Update user-facing daemon toggles and intervals (persisted to preferences.yaml)."""
    daemon_id: str
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None

    _SETTINGS_MAP = {
        "master": {"enabled_key": "daemons.enabled"},
        "heartbeat": {
            "enabled_key": "daemons.heartbeat",
            "interval_key": "daemons.heartbeat_interval_minutes",
            "interval_min": 5,
            "interval_max": 24 * 60,
        },
        "ingestion_sync": {
            "enabled_key": "daemons.ingestion_sync",
            "interval_key": "daemons.ingestion_sync_interval_minutes",
            "interval_min": 15,
            "interval_max": 24 * 60,
        },
        "cron": {"enabled_key": "daemons.cron"},
        "gmail_webhook": {"enabled_key": "daemons.gmail_webhook"},
        "reflection": {
            "enabled_key": "reflection.enabled",
            "interval_key": "reflection.interval_hours",
            "interval_min": 1,
            "interval_max": 168,
            "interval_as_hours": True,
        },
    }

    def validate(self) -> tuple[bool, Optional[str]]:
        if self.daemon_id not in self._SETTINGS_MAP:
            return False, f"Unknown daemon: {self.daemon_id}"
        if self.enabled is None and self.interval_minutes is None:
            return False, "Provide enabled and/or interval_minutes"
        if self.interval_minutes is not None:
            spec = self._SETTINGS_MAP[self.daemon_id]
            if "interval_key" not in spec:
                return False, f"{self.daemon_id} has no configurable interval"
            lo, hi = spec["interval_min"], spec["interval_max"]
            if not (lo <= self.interval_minutes <= hi):
                return False, f"interval_minutes must be between {lo} and {hi}"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.infrastructure.repositories.config_repository import ConfigRepository
        from src.core.monitoring.daemon_status import get_daemon_snapshot

        spec = self._SETTINGS_MAP[self.daemon_id]
        if self.daemon_id == "master":
            if os.getenv("CHAD_DAEMONS_ENABLED", ""):
                return {"success": False, "error": "Autonomous actions are controlled by the server launch configuration."}
        repo = ConfigRepository()

        if self.enabled is not None:
            if not repo.update_preference(spec["enabled_key"], bool(self.enabled)):
                return {"success": False, "error": "Failed to save enabled flag"}

        if self.interval_minutes is not None:
            value = float(self.interval_minutes)
            if spec.get("interval_as_hours"):
                value = self.interval_minutes  # stored as hours for reflection
            if not repo.update_preference(spec["interval_key"], value):
                return {"success": False, "error": "Failed to save interval"}

        from src.shared.config_manager import ConfigManager
        ConfigManager.load_config()

        return {
            "success": True,
            "daemon": next((d for d in get_daemon_snapshot(facade) if d["id"] == self.daemon_id), None),
        }


@dataclass
class ListCronJobsCommand(Command):
    """All cron/at/every tasks (not heartbeat — that's its own checklist)."""

    async def execute(self, facade) -> List[Dict[str, Any]]:
        from src.infrastructure.repositories.task_repository import TaskRepository
        tasks = await TaskRepository().get_all_tasks_async()
        return [_task_summary(t) for t in tasks]


@dataclass
class CreateCronJobCommand(Command):
    """Schedule a new cron/at/every task."""
    name: str
    description: str
    schedule: str
    trigger: str = "cron"
    is_agentic: bool = False

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.name or not self.name.strip():
            return False, "Name is required"
        if not self.description or not self.description.strip():
            return False, "Description/prompt is required"
        if not self.schedule or not self.schedule.strip():
            return False, "Schedule is required"
        if self.trigger not in VALID_CRON_TRIGGERS:
            return False, f"Trigger must be one of: {', '.join(VALID_CRON_TRIGGERS)}"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        cron_scheduler = getattr(facade, "cron_scheduler", None)
        if not cron_scheduler:
            return {"success": False, "error": "Cron scheduler is not running on this server"}

        task_id = await cron_scheduler.add_task(
            name=self.name.strip(),
            description=self.description.strip(),
            schedule=self.schedule.strip(),
            trigger=VALID_CRON_TRIGGERS[self.trigger],
            metadata={"is_agentic": bool(self.is_agentic)},
        )
        return {"success": True, "task_id": task_id}


@dataclass
class SetCronJobStatusCommand(Command):
    """Pause or resume a cron job."""
    task_id: str
    status: str  # "paused" | "scheduled"

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.task_id:
            return False, "task_id is required"
        if self.status not in ("paused", "scheduled"):
            return False, "status must be 'paused' or 'scheduled'"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.infrastructure.repositories.task_repository import TaskRepository
        repo = TaskRepository()
        task = await repo.get_task_async(self.task_id)
        if not task:
            return {"success": False, "error": "Task not found"}
        task.status = TaskStatus(self.status)
        ok = await repo.update_task_async(task)
        return {"success": ok}


@dataclass
class DeleteCronJobCommand(Command):
    """Delete a cron job and its execution history."""
    task_id: str

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.task_id:
            return False, "task_id is required"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.infrastructure.repositories.task_repository import TaskRepository
        ok = await TaskRepository().delete_task_async(self.task_id)
        return {"success": ok}


@dataclass
class UpdateHeartbeatChecklistCommand(Command):
    """Edit what the heartbeat checks every cycle (versioned — see persona_config)."""
    content: str

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.content or not self.content.strip():
            return False, "Checklist content cannot be empty"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.infrastructure.repositories.memory_repository import MemoryRepository
        ok = MemoryRepository().update_persona_config("heartbeat", self.content.strip())
        return {"success": ok}


@dataclass
class GetWebhookStatusCommand(Command):
    """Per-service (gmail/telegram/github/calendar) configured + last-triggered status."""

    async def execute(self, facade) -> Dict[str, Any]:
        from src.remote.webhooks.webhook_manager import get_webhook_manager
        return get_webhook_manager().get_status()


_ingestion_sync_lock: Optional[asyncio.Lock] = None


def _get_ingestion_sync_lock() -> asyncio.Lock:
    global _ingestion_sync_lock
    if _ingestion_sync_lock is None:
        _ingestion_sync_lock = asyncio.Lock()
    return _ingestion_sync_lock


@dataclass
class TriggerIngestionSyncCommand(Command):
    """Manually trigger an ingestion sync cycle (e.g. Chrome browser history) immediately."""
    source: str = "chrome"

    async def execute(self, facade) -> Dict[str, Any]:
        from src.core.intelligence.memory_engine import MemoryEngine
        from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager
        from src.core.intelligence.ingestion.chrome import ChromeHistoryIngestor

        lock = _get_ingestion_sync_lock()
        if lock.locked():
            return {
                "success": True,
                "status": "running",
                "added_count": 0,
                "duplicate_count": 0,
                "message": "A sync is already running in the background.",
                "report": {},
            }

        memory = getattr(facade, "memory_engine", None)
        if not memory and hasattr(facade, "assistant"):
            memory = getattr(facade.assistant, "memory", None)
        if not memory:
            memory = MemoryEngine()

        manager = IngestionSyncManager(memory)
        if self.source == "chrome":
            manager.register_ingestor(ChromeHistoryIngestor())
        else:
            manager.register_ingestor(ChromeHistoryIngestor())

        async with lock:
            report = await asyncio.to_thread(manager.run_sync_cycle)
        chrome_report = report.get("sources", {}).get(self.source, report.get("sources", {}).get("chrome", {}))
        status = chrome_report.get("status", "unknown")
        added = chrome_report.get("added", 0)
        duplicates = chrome_report.get("duplicates", 0)

        if status == "success":
            message = f"Synced {added} new item{'s' if added != 1 else ''} ({duplicates} duplicates skipped)."
        elif status == "partial":
            message = f"Partially synced {added} items (interrupted or rate limited)."
        elif status == "failed":
            message = f"Sync failed: {chrome_report.get('error', 'Unknown error')}"
        else:
            message = f"Sync completed with status: {status} (+{added})."

        return {
            "success": status in ("success", "partial"),
            "status": status,
            "added_count": added,
            "duplicate_count": duplicates,
            "message": message,
            "report": report,
        }

