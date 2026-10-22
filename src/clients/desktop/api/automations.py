from src.application.commands import (
    GetAutomationsSummaryCommand,
    ListAutonomousRunsCommand,
    UpdateDaemonSettingsCommand,
    ListCronJobsCommand,
    CreateCronJobCommand,
    SetCronJobStatusCommand,
    DeleteCronJobCommand,
    UpdateHeartbeatChecklistCommand,
    GetWebhookStatusCommand,
    TriggerIngestionSyncCommand,
)


class AutomationsAPIMixin:
    """Mixin for the Autonomous Actions settings panel: heartbeat, cron, webhooks."""

    def get_automations_summary(self):
        """Heartbeat status+checklist, cron task count, webhook status — what wakes Chad."""
        return self._execute_command(
            GetAutomationsSummaryCommand(), timeout=15.0, default_fallback=None,
        )

    def list_cron_jobs(self):
        """All scheduled cron/at/every tasks."""
        return self._execute_command(ListCronJobsCommand(), timeout=15.0, default_fallback=[])

    def create_cron_job(self, name, description, schedule, trigger="cron", is_agentic=False):
        """Schedule a new cron/at/every task."""
        return self._execute_command(
            CreateCronJobCommand(
                name=name, description=description, schedule=schedule,
                trigger=trigger, is_agentic=bool(is_agentic),
            ),
            timeout=15.0, default_fallback={"success": False, "error": "Command timed out"},
        )

    def set_cron_job_status(self, task_id, status):
        """Pause ('paused') or resume ('scheduled') a cron job."""
        return self._execute_command(
            SetCronJobStatusCommand(task_id=task_id, status=status),
            timeout=15.0, default_fallback={"success": False},
        )

    def delete_cron_job(self, task_id):
        """Delete a cron job and its execution history."""
        return self._execute_command(
            DeleteCronJobCommand(task_id=task_id), timeout=15.0, default_fallback={"success": False},
        )

    def update_heartbeat_checklist(self, content):
        """Edit what the heartbeat checks every cycle."""
        return self._execute_command(
            UpdateHeartbeatChecklistCommand(content=content),
            timeout=15.0, default_fallback={"success": False},
        )

    def get_webhook_status(self):
        """Per-service (gmail/telegram/github/calendar) configured + last-triggered status."""
        return self._execute_command(GetWebhookStatusCommand(), timeout=15.0, default_fallback={})

    def list_autonomous_runs(self, limit=50, source=None):
        """Recent autonomous run log (heartbeat, cron, sync, webhooks, reflection)."""
        return self._execute_command(
            ListAutonomousRunsCommand(limit=int(limit), source=source),
            timeout=15.0, default_fallback=[],
        )

    def update_daemon_settings(self, daemon_id, enabled=None, interval_minutes=None):
        """Toggle a daemon or change its interval (saved to preferences.yaml)."""
        return self._execute_command(
            UpdateDaemonSettingsCommand(
                daemon_id=daemon_id,
                enabled=enabled if enabled is None else bool(enabled),
                interval_minutes=int(interval_minutes) if interval_minutes is not None else None,
            ),
            timeout=15.0, default_fallback={"success": False, "error": "Command timed out"},
        )

    def trigger_ingestion_sync(self, source="chrome"):
        """Manually trigger an ingestion sync cycle (e.g. Chrome history) immediately."""
        return self._execute_command(
            TriggerIngestionSyncCommand(source=str(source)),
            timeout=60.0, default_fallback={"success": False, "error": "Sync command timed out"},
        )

