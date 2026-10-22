"""CronScheduler — schedule-calculation logic and task-execution resilience.

issue #216 gap: tests/scenarios/test_heartbeat_cron_flow.py exercises the real orchestrator
end-to-end (requires live creds), but src/core/scheduling/cron_scheduler.py had no isolated
unit coverage of its pure scheduling math (CRON/AT/EVERY next-run calculation) or its
per-task error resilience (one failing task must not take down the scheduler loop or leave
other due tasks unprocessed).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.scheduling.cron_scheduler import CronScheduler
from src.domain.models import AgentTask, TaskStatus, TaskTrigger


class _FakeTaskRepo:
    def __init__(self, tasks=None, running_tasks=None):
        self.tasks = list(tasks or [])
        self._running_tasks = list(running_tasks or [])
        self.updated: list[AgentTask] = []

    async def get_running_tasks(self):
        return self._running_tasks

    async def update_task_async(self, task):
        self.updated.append(task)
        return True

    async def get_due_tasks_async(self, current_time=None):
        return self.tasks

    async def get_all_tasks_async(self):
        return self.tasks


def _scheduler(orchestrator=None, repo=None):
    if orchestrator is None:
        orchestrator = MagicMock()
        orchestrator.process_request = AsyncMock(return_value="ok")
    repo = repo or _FakeTaskRepo()
    return CronScheduler(orchestrator, repo, event_bus=MagicMock()), orchestrator, repo


def _task(trigger, schedule=None, status=TaskStatus.SCHEDULED, metadata=None, **kw) -> AgentTask:
    return AgentTask(
        task_id="t1", name="Test Task", description="do the thing",
        trigger=trigger, schedule=schedule, status=status,
        metadata=metadata or {}, **kw,
    )


# --- pure schedule-calculation math ----------------------------------------- #

def test_calculate_cron_next_run_matches_expression():
    svc, _, _ = _scheduler()
    next_run = svc._calculate_cron_next_run("0 9 * * *")  # 9am daily
    assert next_run.hour == 9 and next_run.minute == 0
    assert next_run > datetime.now()


def test_calculate_at_next_run_future_returns_datetime():
    svc, _, _ = _scheduler()
    future = (datetime.now() + timedelta(days=1)).isoformat()
    assert svc._calculate_at_next_run(future) is not None


def test_calculate_at_next_run_past_returns_none():
    svc, _, _ = _scheduler()
    past = (datetime.now() - timedelta(days=1)).isoformat()
    assert svc._calculate_at_next_run(past) is None


@pytest.mark.parametrize("interval,delta", [
    ("30m", timedelta(minutes=30)),
    ("2h", timedelta(hours=2)),
    ("1d", timedelta(days=1)),
])
def test_calculate_every_next_run_from_last_run(interval, delta):
    svc, _, _ = _scheduler()
    last_run = datetime(2026, 1, 1, 12, 0, 0)
    result = svc._calculate_every_next_run(interval, last_run=last_run)
    assert result == last_run + delta


def test_calculate_every_next_run_first_run_uses_now():
    svc, _, _ = _scheduler()
    before = datetime.now()
    result = svc._calculate_every_next_run("10m", last_run=None)
    assert before <= result - timedelta(minutes=10) <= datetime.now()


def test_calculate_every_next_run_invalid_format_raises():
    svc, _, _ = _scheduler()
    with pytest.raises(ValueError, match="Invalid interval format"):
        svc._calculate_every_next_run("banana", last_run=None)


def test_calculate_next_run_dispatches_to_correct_calculator():
    svc, _, _ = _scheduler()
    at_time = (datetime.now() + timedelta(hours=1)).isoformat()
    assert svc._calculate_next_run(at_time, TaskTrigger.AT) is not None
    assert svc._calculate_next_run("15m", TaskTrigger.EVERY, last_run=None) is not None
    assert svc._calculate_next_run("0 * * * *", TaskTrigger.CRON) is not None


def test_calculate_next_run_unsupported_trigger_returns_none():
    svc, _, _ = _scheduler()
    assert svc._calculate_next_run("whatever", TaskTrigger.USER) is None


def test_calculate_next_run_falls_back_to_plus_one_hour_on_bad_schedule():
    """A malformed cron expression must not crash task scheduling — degrade to a
    safe default retry window instead."""
    svc, _, _ = _scheduler()
    before = datetime.now()
    result = svc._calculate_next_run("not a cron expression", TaskTrigger.CRON)
    assert result is not None
    assert (result - before) >= timedelta(minutes=55)  # ~1 hour fallback


# --- _execute_task: rescheduling + failure resilience ------------------------ #

@pytest.mark.asyncio
async def test_execute_task_recurring_every_reschedules_and_stays_scheduled():
    svc, orchestrator, repo = _scheduler()
    task = _task(TaskTrigger.EVERY, schedule="30m")

    await svc._execute_task(task)

    assert task.status == TaskStatus.SCHEDULED
    assert task.next_run is not None
    orchestrator.process_request.assert_called_once()


@pytest.mark.asyncio
async def test_execute_task_one_time_at_completes_without_reschedule():
    svc, orchestrator, repo = _scheduler()
    past_at = (datetime.now() - timedelta(minutes=1)).isoformat()
    task = _task(TaskTrigger.AT, schedule=past_at)

    await svc._execute_task(task)

    assert task.status == TaskStatus.COMPLETED
    assert task.next_run is None


@pytest.mark.asyncio
async def test_execute_task_orchestrator_failure_marks_task_failed_not_crash():
    orchestrator = MagicMock()
    orchestrator.process_request = AsyncMock(side_effect=RuntimeError("model unreachable"))
    svc, orchestrator, repo = _scheduler(orchestrator=orchestrator)
    task = _task(TaskTrigger.EVERY, schedule="30m")

    await svc._execute_task(task)  # must not raise

    assert task.status == TaskStatus.FAILED
    assert "model unreachable" in task.metadata.get("error", "")


@pytest.mark.asyncio
async def test_execute_task_notification_wraps_prompt_when_not_agentic():
    svc, orchestrator, repo = _scheduler()
    task = _task(TaskTrigger.EVERY, schedule="30m", metadata={"is_agentic": False})
    task.description = "Take out the trash"

    await svc._execute_task(task)

    _, kwargs = orchestrator.process_request.call_args
    assert "SYSTEM NOTIFICATION" in kwargs["prompt"]
    assert "Take out the trash" in kwargs["prompt"]
    assert "Do NOT call any tools" in kwargs["prompt"]


@pytest.mark.asyncio
async def test_execute_task_agentic_uses_raw_description_as_prompt():
    svc, orchestrator, repo = _scheduler()
    task = _task(TaskTrigger.EVERY, schedule="30m", metadata={"is_agentic": True})
    task.description = "Research competitor pricing and summarize"

    await svc._execute_task(task)

    _, kwargs = orchestrator.process_request.call_args
    assert kwargs["prompt"] == "Research competitor pricing and summarize"


# --- _check_and_execute_tasks: one failure doesn't block the batch ----------- #

@pytest.mark.asyncio
async def test_check_and_execute_tasks_one_failure_does_not_block_others():
    orchestrator = MagicMock()
    orchestrator.process_request = AsyncMock(side_effect=[RuntimeError("boom"), "ok"])
    bad_task = _task(TaskTrigger.EVERY, schedule="30m")
    bad_task.task_id = "bad"
    good_task = _task(TaskTrigger.EVERY, schedule="30m")
    good_task.task_id = "good"
    repo = _FakeTaskRepo(tasks=[bad_task, good_task])
    svc, orchestrator, repo = _scheduler(orchestrator=orchestrator, repo=repo)

    await svc._check_and_execute_tasks()

    assert orchestrator.process_request.call_count == 2
    assert bad_task.status == TaskStatus.FAILED
    assert good_task.status == TaskStatus.SCHEDULED  # rescheduled after success


# --- live daemon-enabled toggle (#473: GUI toggle must stop a running loop) -- #

@pytest.mark.asyncio
async def test_scheduler_loop_skips_execution_when_daemon_disabled_live():
    """Toggling daemons.cron off mid-session (no restart) must stop the scheduler
    from executing due tasks on its very next cycle, not just at next process boot."""
    svc, orchestrator, repo = _scheduler()
    svc._running = True

    ticks = {"n": 0}

    async def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            svc._running = False  # stop the while-loop after a few cycles

    with patch("asyncio.sleep", new=fake_sleep), \
         patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=False), \
         patch.object(svc, "_check_and_execute_tasks", new=AsyncMock()) as check_mock:
        await svc._scheduler_loop()

    check_mock.assert_not_called()
    assert ticks["n"] == 3, "loop must keep cycling (task stays alive) while paused"


@pytest.mark.asyncio
async def test_scheduler_loop_executes_when_daemon_enabled_live():
    """Sanity counterpart: with the flag live-enabled, the loop still checks for
    due tasks (proves the new check doesn't silently swallow the enabled case)."""
    svc, orchestrator, repo = _scheduler()
    svc._running = True

    ticks = {"n": 0}

    async def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] >= 1:
            svc._running = False

    with patch("asyncio.sleep", new=fake_sleep), \
         patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=True), \
         patch.object(svc, "_check_and_execute_tasks", new=AsyncMock()) as check_mock:
        await svc._scheduler_loop()

    check_mock.assert_called_once()


@pytest.mark.asyncio
async def test_get_status_online_when_running_and_daemon_enabled():
    svc, _, _ = _scheduler()
    svc._running = True
    with patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=True):
        status = await svc.get_status()
    assert status["status"] == "online"


@pytest.mark.asyncio
async def test_get_status_paused_when_running_but_daemon_disabled():
    """GUI-visible signal that the toggle took effect without a restart: the
    asyncio task is still alive ('running'), but Automations shows it paused."""
    svc, _, _ = _scheduler()
    svc._running = True
    with patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=False):
        status = await svc.get_status()
    assert status["status"] == "paused"


@pytest.mark.asyncio
async def test_get_status_offline_when_not_running():
    svc, _, _ = _scheduler()
    svc._running = False
    with patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=True):
        status = await svc.get_status()
    assert status["status"] == "offline"


# --- _recover_stuck_tasks: crash recovery ------------------------------------ #

@pytest.mark.asyncio
async def test_recover_stuck_tasks_resets_running_every_task_and_recalculates_next_run():
    stuck = _task(TaskTrigger.EVERY, schedule="30m", status=TaskStatus.RUNNING)
    repo = _FakeTaskRepo(running_tasks=[stuck])
    svc, _, repo = _scheduler(repo=repo)

    await svc._recover_stuck_tasks()

    assert stuck.status == TaskStatus.SCHEDULED
    assert stuck.next_run is not None
    assert repo.updated == [stuck]


@pytest.mark.asyncio
async def test_recover_stuck_tasks_leaves_at_tasks_next_run_untouched():
    original_next_run = datetime.now() + timedelta(hours=2)
    stuck = _task(TaskTrigger.AT, schedule=original_next_run.isoformat(), status=TaskStatus.RUNNING)
    stuck.next_run = original_next_run
    repo = _FakeTaskRepo(running_tasks=[stuck])
    svc, _, repo = _scheduler(repo=repo)

    await svc._recover_stuck_tasks()

    assert stuck.status == TaskStatus.SCHEDULED
    assert stuck.next_run == original_next_run


@pytest.mark.asyncio
async def test_recover_stuck_tasks_noop_when_nothing_stuck():
    repo = _FakeTaskRepo(running_tasks=[])
    svc, _, repo = _scheduler(repo=repo)

    await svc._recover_stuck_tasks()  # must not raise

    assert repo.updated == []
