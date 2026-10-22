"""
Cron Job Scheduler Service

Executes scheduled tasks at specified intervals using cron expressions.
Integrates with ConversationOrchestrator for SYSTEM-initiated turns.
"""

import asyncio
import uuid
import re
from datetime import datetime, timedelta
from typing import Optional, List, TYPE_CHECKING
from croniter import croniter

from src.shared.logger import setup_logger
from src.infrastructure.repositories.task_repository import TaskRepository
from src.domain.models import AgentTask, TaskStatus, TaskTrigger
from src.application.events import EventBus, CronTaskTriggered

if TYPE_CHECKING:
    from src.core.orchestration.conversation_orchestrator import ConversationOrchestrator

class CronScheduler:
    """
    Cron job scheduler that executes tasks based on cron expressions.

    Polls TaskRepository every 60 seconds for due tasks and triggers
    execution via ConversationOrchestrator with SYSTEM initiator.
    """
    def __init__(self, orchestrator: "ConversationOrchestrator", task_repo: TaskRepository, event_bus: Optional[EventBus] = None):
        self._logger = setup_logger("CronScheduler")
        self._orchestrator = orchestrator
        self._task_repo = task_repo
        self._event_bus = event_bus or (orchestrator.event_bus if hasattr(orchestrator, 'event_bus') else EventBus())
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the cron scheduler loop."""
        if self._running:
            self._logger.warning("Scheduler already running")
            return

        await self._recover_stuck_tasks()
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self._logger.info("Cron scheduler started")

    async def _recover_stuck_tasks(self):
        """Reset tasks left in 'running' status from a previous server crash back to
        'scheduled' so they fire on the next due cycle rather than being silently lost."""
        try:
            running_tasks = await self._task_repo.get_running_tasks()
            if not running_tasks:
                return
            self._logger.warning(
                f"[ RECOVERY ] Found {len(running_tasks)} task(s) stuck in 'running' — resetting to 'scheduled'"
            )
            for task in running_tasks:
                task.status = TaskStatus.SCHEDULED
                # Recalculate next_run so it fires promptly (or on its natural next slot)
                if task.trigger != TaskTrigger.AT:
                    task.next_run = self._calculate_next_run(task.schedule, task.trigger, task.last_run)
                await self._task_repo.update_task_async(task)
        except Exception as e:
            self._logger.error(f"[ RECOVERY ] Failed to recover stuck tasks: {e}", exc_info=True)

    async def stop(self):
        """Stop the cron scheduler loop."""
        self._running = False

        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        self._logger.info("Cron scheduler stopped")

    async def _scheduler_loop(self):
        """Main scheduler loop - sleeps until next task is due (OpenClaw pattern).

        Instead of polling, calculates exact time until next task and sleeps precisely.
        This ensures "remind me in 1 minute" fires exactly on time.
        """
        MAX_SLEEP = 60.0  # Maximum sleep duration (1 minute) to detect new tasks

        while self._running:
            try:
                # Re-read the enabled flag each cycle (daemons.cron AND the master
                # daemons.enabled switch / CHAD_DAEMONS_ENABLED env override), same
                # live-reload pattern as HeartbeatService's interval re-read. Lets the
                # Settings -> Automations GUI toggle actually pause a live cron loop
                # without a process restart (#473) — the asyncio task keeps sleeping so
                # re-enabling is instant, but no tasks execute while disabled.
                from src.core.monitoring.daemon_status import daemon_enabled
                if not daemon_enabled("cron"):
                    await asyncio.sleep(MAX_SLEEP)
                    continue

                # Execute any tasks that are currently due
                await self._check_and_execute_tasks()

                # Calculate how long to sleep until next task
                sleep_duration = await self._calculate_sleep_duration(MAX_SLEEP)

                # Sleep until next task is due (or max sleep time)
                await asyncio.sleep(sleep_duration)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Scheduler loop error: {e}", exc_info=True)
                # On error, sleep briefly before retrying
                await asyncio.sleep(5.0)

    async def _calculate_sleep_duration(self, max_sleep: float) -> float:
        """Calculate seconds to sleep until next task is due.

        OpenClaw pattern: Sleep exactly until next task, not fixed intervals.

        Returns:
            Seconds to sleep (capped at max_sleep)
        """
        try:
            now = datetime.now()

            # Get all scheduled tasks (async)
            all_tasks = await self._task_repo.get_all_tasks_async()

            # Find the earliest next_run time
            earliest_run = None
            for task in all_tasks:
                if task.status == TaskStatus.SCHEDULED and task.next_run:
                    if earliest_run is None or task.next_run < earliest_run:
                        earliest_run = task.next_run

            if earliest_run is None:
                # No scheduled tasks - sleep for max duration
                return max_sleep

            # Ensure timezone compatibility
            if earliest_run.tzinfo is not None:
                now = now.astimezone(earliest_run.tzinfo)

            # Calculate time until earliest task
            time_until = (earliest_run - now).total_seconds()

            if time_until <= 0:
                # Task is overdue - execute immediately
                return 0.0

            # Return sleep duration, capped at max_sleep
            sleep_duration = min(time_until, max_sleep)


            return sleep_duration

        except Exception as e:
            self._logger.error(f"Error calculating sleep duration: {e}")
            return max_sleep  # Default to max sleep on error

    async def _check_and_execute_tasks(self):
        """Check TaskRepository for due tasks and execute them."""
        now = datetime.now()

        # Get all scheduled tasks that are due (async)
        due_tasks = await self._task_repo.get_due_tasks_async(current_time=now)

        if not due_tasks:
            return

        self._logger.info(f"Found {len(due_tasks)} due tasks to execute")

        # Execute each task
        for task in due_tasks:
            try:
                await self._execute_task(task)
            except Exception as e:
                self._logger.error(f"Failed to execute task {task.task_id}: {e}", exc_info=True)

    async def _execute_task(self, task: AgentTask):
        """
        Execute a single cron task via ConversationOrchestrator.

        Args:
            task: AgentTask from TaskRepository
        """
        self._logger.info(f"Executing cron task: {task.name} (ID: {task.task_id})")

        # Mark task as running
        task.mark_running()
        await self._task_repo.update_task_async(task)

        try:
            # Extract routing info from metadata
            stored_session_key = task.metadata.get("session_key", "global")
            target_platform = task.metadata.get("target_platform", "auto")
            
            final_session_key = stored_session_key
            final_platform = "system" # Base platform for cron

            # Smart Routing Logic
            if target_platform == "telegram":
                # Force telegram routing
                if not (stored_session_key and ":telegram:" in stored_session_key):
                    # Need to find a telegram session key if the task was created elsewhere
                    from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository
                    chat_repo = TelegramChatRepository()
                    default_chat_id = chat_repo.get_default_chat_id()
                    if default_chat_id:
                        final_session_key = chat_repo.get_session_key(default_chat_id)
                        self._logger.info(f"Routing task {task.name} to Telegram session: {final_session_key}")
            
            elif target_platform == "pc":
                # Force PC routing
                from src.core.intelligence.session_key_manager import SessionKeyManager
                final_session_key = SessionKeyManager.DEFAULT_SESSION_KEY
                self._logger.info(f"Routing task {task.name} to PC session")

            # Trigger turn via ConversationOrchestrator's full ReAct loop
            from src.shared.context import RequestContext
            ctx = RequestContext(platform=final_platform, session_id=final_session_key)
            
            # If this is a passive reminder (is_agentic=False), wrap it in a notification prompt
            # to prevent model confusion and recursive loops.
            is_agentic = task.metadata.get("is_agentic", False)
            prompt = task.description
            
            if not is_agentic:
                # Load prompt template from PromptRegistry
                try:
                    from src.infrastructure.repositories.prompt_registry import PromptRegistry
                    template = PromptRegistry.get_instance().get_prompt("event_reminder")
                    prompt = template.replace("{task_description}", task.description)
                except Exception:
                    prompt = (
                        f'[SYSTEM NOTIFICATION] A scheduled reminder has fired: "{task.description}". '
                        "Please deliver this notification to the user naturally in lowercase. "
                        "IMPORTANT: This is a notification turn. Do NOT call any tools or schedule new reminders."
                    )
            # process_request handles start_turn, model loop, and finalize_turn
            from src.core.orchestration.conversation_orchestrator import TurnInitiator
            response_text = await self._orchestrator.process_request(
                prompt=prompt,
                context=ctx,
                initiator=TurnInitiator.SYSTEM,
                category='autonomous'
            )

            # Publish trigger event for external handlers (backwards compatibility/logging)
            self._event_bus.publish(CronTaskTriggered(
                task_id=task.task_id,
                task_name=task.name,
                description=task.description,
                session_key=final_session_key,
                metadata=task.metadata
            ))

            # Mark task as completed
            task.mark_completed()
            self._logger.info(f"Cron task {task.name} execution finished. Response: {response_text[:50]}...")

            # Calculate next run time based on trigger type
            if task.schedule:
                next_run = self._calculate_next_run(
                    schedule=task.schedule,
                    trigger=task.trigger,
                    last_run=task.last_run
                )

                if next_run:
                    # Recurring task - schedule next run
                    task.next_run = next_run
                    task.status = TaskStatus.SCHEDULED
                    self._logger.info(f"Task {task.name} completed. Next run: {next_run}")
                else:
                    # One-time task completed (AT trigger) - no next run
                    task.next_run = None
                    task.status = TaskStatus.COMPLETED
                    self._logger.info(f"Task {task.name} completed (one-time task, no next run)")

            await self._task_repo.update_task_async(task)

        except Exception as e:
            # Mark task as failed
            task.mark_failed(error=str(e))
            await self._task_repo.update_task_async(task)
            self._logger.error(f"Task {task.name} failed: {e}", exc_info=True)

    def _calculate_next_run(
        self,
        schedule: str,
        trigger: TaskTrigger,
        last_run: Optional[datetime] = None
    ) -> Optional[datetime]:
        """
        Calculate the next run time based on trigger type and schedule.

        Args:
            schedule: Schedule string (cron expression, ISO datetime, or interval)
            trigger: Task trigger type (CRON, AT, or EVERY)
            last_run: Last execution time (for EVERY intervals)

        Returns:
            Next scheduled run time, or None for one-time tasks that have run
        """
        try:
            if trigger == TaskTrigger.CRON:
                return self._calculate_cron_next_run(schedule)
            elif trigger == TaskTrigger.AT:
                return self._calculate_at_next_run(schedule)
            elif trigger == TaskTrigger.EVERY:
                return self._calculate_every_next_run(schedule, last_run)
            else:
                self._logger.warning(f"Unsupported trigger type for scheduling: {trigger}")
                return None

        except Exception as e:
            self._logger.error(f"Failed to calculate next run for {trigger.value} schedule '{schedule}': {e}")
            return datetime.now() + timedelta(hours=1)  # Default fallback

    def _calculate_cron_next_run(self, cron_expression: str) -> datetime:
        """
        Calculate next run for CRON schedule.

        Args:
            cron_expression: Standard cron expression (e.g., "0 9 * * *")

        Returns:
            Next scheduled run time
        """
        cron = croniter(cron_expression, datetime.now())
        return cron.get_next(datetime)

    def _calculate_at_next_run(self, at_time: str) -> Optional[datetime]:
        """
        Calculate next run for AT schedule (one-time execution).

        Args:
            at_time: ISO 8601 datetime string (e.g., "2026-03-17T15:30:00")

        Returns:
            Scheduled datetime, or None if already passed
        """
        scheduled_time = datetime.fromisoformat(at_time)

        # If time has passed, return None (one-time task completed)
        if scheduled_time <= datetime.now():
            return None

        return scheduled_time

    def _calculate_every_next_run(
        self,
        interval: str,
        last_run: Optional[datetime] = None
    ) -> datetime:
        """
        Calculate next run for EVERY schedule (interval-based).

        Args:
            interval: Interval string (e.g., "30m", "2h", "1d")
            last_run: Last execution time (None for first run)

        Returns:
            Next scheduled run time

        Raises:
            ValueError: If interval format is invalid
        """
        # Parse interval (e.g., "30m", "2h", "1d")
        match = re.match(r'^(\d+)(m|h|d)$', interval)
        if not match:
            raise ValueError(
                f"Invalid interval format: {interval}. "
                f"Expected format: <number><unit> where unit is m (minutes), h (hours), or d (days). "
                f"Examples: '30m', '2h', '1d'"
            )

        value = int(match.group(1))
        unit = match.group(2)

        # Calculate timedelta
        if unit == 'm':
            delta = timedelta(minutes=value)
        elif unit == 'h':
            delta = timedelta(hours=value)
        elif unit == 'd':
            delta = timedelta(days=value)
        else:
            raise ValueError(f"Unsupported interval unit: {unit}")

        # Calculate next run from last_run or now
        base_time = last_run or datetime.now()
        return base_time + delta

    async def add_task(
        self,
        name: str,
        description: str,
        schedule: str,
        trigger: TaskTrigger = TaskTrigger.CRON,
        metadata: dict = None
    ) -> str:
        """
        Add a new scheduled task.

        Args:
            name: Task name
            description: What the task should do
            schedule: Schedule string (format depends on trigger type):
                - CRON: Cron expression (e.g., "0 9 * * *" for 9am daily)
                - AT: ISO 8601 datetime (e.g., "2026-03-17T15:30:00")
                - EVERY: Interval (e.g., "30m" for every 30 minutes)
            trigger: Task trigger type (CRON, AT, or EVERY)
            metadata: Optional metadata dictionary

        Returns:
            task_id

        Examples:
            >>> await scheduler.add_task("Morning briefing", "Summarize news", "0 9 * * *", TaskTrigger.CRON)
            >>> await scheduler.add_task("One-time reminder", "Check status", "2026-03-17T15:30:00", TaskTrigger.AT)
            >>> await scheduler.add_task("Periodic check", "Health check", "30m", TaskTrigger.EVERY)
        """
        task_id = str(uuid.uuid4())

        # Calculate next run
        next_run = self._calculate_next_run(schedule, trigger)

        task = AgentTask(
            task_id=task_id,
            name=name,
            description=description,
            trigger=trigger,
            schedule=schedule,
            status=TaskStatus.SCHEDULED,
            next_run=next_run,
            metadata=metadata or {}
        )

        success = await self._task_repo.create_task_async(task)
        if success:
            self._logger.info(
                f"Added {trigger.value} task '{name}' with schedule '{schedule}'. "
                f"Next run: {task.next_run}"
            )
            return task_id
        else:
            raise Exception(f"Failed to create task '{name}'")

    async def list_tasks(self) -> List[AgentTask]:
        """List all scheduled tasks."""
        return await self._task_repo.get_scheduled_tasks_async()

    async def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Get a task by ID."""
        return await self._task_repo.get_task_async(task_id)

    async def remove_task(self, task_id: str) -> bool:
        """Remove a scheduled task."""
        success = await self._task_repo.delete_task_async(task_id)
        if success:
            self._logger.info(f"Removed cron task {task_id}")
        return success

    async def get_due_tasks(self) -> List[AgentTask]:
        """Get tasks that are currently due to run."""
        return await self._task_repo.get_due_tasks_async()

    async def get_status(self) -> dict:
        """
        Get current scheduler status (OpenClaw Command Ladder pattern).
        
        Returns:
            Dict with status, next wake time, and task list.
        """
        all_tasks = await self._task_repo.get_all_tasks_async()
        scheduled_tasks = [t for t in all_tasks if t.status == TaskStatus.SCHEDULED]

        # Calculate next wake at
        next_wake = None
        for task in scheduled_tasks:
            if not next_wake or (task.next_run and task.next_run < next_wake):
                next_wake = task.next_run
                next_wake = task.next_run

        from src.core.monitoring.daemon_status import daemon_enabled
        if not self._running:
            status = "offline"
        elif daemon_enabled("cron"):
            status = "online"
        else:
            status = "paused"

        return {
            "status": status,
            "task_count": len(scheduled_tasks),
            "next_wake_at": next_wake.isoformat() if next_wake else None,
            "tasks": [
                {
                    "id": t.task_id,
                    "name": t.name,
                    "next_run": t.next_run.isoformat() if t.next_run else None,
                    "trigger": t.trigger.value
                } for t in scheduled_tasks
            ]
        }
