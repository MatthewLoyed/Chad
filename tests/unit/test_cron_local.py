"""
Parity tests for TaskRepository + CronScheduler over LocalPostgrestShim (issue #466).

Runs the exact repository/scheduler code with Supabase unconfigured, proving
`agent_tasks` create/list-due/update/recover all work offline against the
sqlite-backed shim (issue #465) with no code fork.
"""

import importlib
import sys
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_store, local_postgrest
from src.domain.models import AgentTask, TaskStatus, TaskTrigger


@pytest.fixture
def repo_module(tmp_path, monkeypatch):
    """Fresh sqlite file + fresh shim singleton + fresh SupabaseManager singleton,
    with credentials removed so every verb routes through the local shim. Reloads
    task_repository so its module-level `register_table` calls target the fresh
    shim instance (the singleton is normally created once per process)."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    db = tmp_path / "cron_local.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    import src.infrastructure.repositories.task_repository as task_repo_mod
    importlib.reload(task_repo_mod)
    return task_repo_mod


def _make_task(task_id, status=TaskStatus.SCHEDULED, next_run=None, trigger=TaskTrigger.EVERY, schedule="30m"):
    return AgentTask(
        task_id=task_id,
        name=f"task-{task_id}",
        description="do the thing",
        trigger=trigger,
        schedule=schedule,
        status=status,
        next_run=next_run,
    )


# --- create / due / update round-trip ---------------------------------------

async def test_create_persists_and_supabase_disabled(repo_module):
    repo = repo_module.TaskRepository()
    assert repo.supabase.enabled is False

    task = _make_task("t1", next_run=datetime.now() - timedelta(seconds=1))
    assert await repo.create_task(task) is True

    got = await repo.get_task("t1")
    assert got is not None
    assert got.name == "task-t1"
    assert got.status == TaskStatus.SCHEDULED


async def test_get_due_tasks_fires_when_due(repo_module):
    repo = repo_module.TaskRepository()

    past = _make_task("due", next_run=datetime.now() - timedelta(minutes=1))
    future = _make_task("not-due", next_run=datetime.now() + timedelta(hours=1))
    await repo.create_task(past)
    await repo.create_task(future)

    due = await repo.get_due_tasks(current_time=datetime.now())
    assert [t.task_id for t in due] == ["due"]


async def test_status_transitions_persist(repo_module):
    repo = repo_module.TaskRepository()
    task = _make_task("t2", next_run=datetime.now())
    await repo.create_task(task)

    task.mark_running()
    assert await repo.update_task(task) is True
    running = await repo.get_task("t2")
    assert running.status == TaskStatus.RUNNING

    task.mark_completed()
    task.next_run = None
    assert await repo.update_task(task) is True
    completed = await repo.get_task("t2")
    assert completed.status == TaskStatus.COMPLETED
    assert completed.next_run is None


# --- Brain-restart survival ---------------------------------------------------

async def test_tasks_survive_repository_restart(repo_module):
    """A fresh TaskRepository instance (simulating a Brain restart) sees the
    same sqlite-backed data — next_run and status included."""
    repo_a = repo_module.TaskRepository()
    next_run = datetime.now() + timedelta(minutes=5)
    task = _make_task("persist-me", status=TaskStatus.SCHEDULED, next_run=next_run)
    await repo_a.create_task(task)

    repo_b = repo_module.TaskRepository()
    reloaded = await repo_b.get_task("persist-me")
    assert reloaded is not None
    assert reloaded.status == TaskStatus.SCHEDULED
    assert reloaded.next_run == next_run


# --- recover stuck tasks -------------------------------------------------------

async def test_recover_stuck_tasks_resets_running_to_scheduled(repo_module):
    repo = repo_module.TaskRepository()
    stuck = _make_task("stuck", status=TaskStatus.RUNNING, trigger=TaskTrigger.EVERY, schedule="30m")
    await repo.create_task(stuck)

    from src.core.scheduling.cron_scheduler import CronScheduler
    scheduler = CronScheduler(orchestrator=MagicMock(), task_repo=repo, event_bus=MagicMock())

    await scheduler._recover_stuck_tasks()

    recovered = await repo.get_task("stuck")
    assert recovered.status == TaskStatus.SCHEDULED
    assert recovered.next_run is not None


async def test_recover_stuck_tasks_noop_when_none_running(repo_module):
    repo = repo_module.TaskRepository()
    scheduled = _make_task("fine", status=TaskStatus.SCHEDULED, next_run=datetime.now() + timedelta(hours=1))
    await repo.create_task(scheduled)

    from src.core.scheduling.cron_scheduler import CronScheduler
    scheduler = CronScheduler(orchestrator=MagicMock(), task_repo=repo, event_bus=MagicMock())
    await scheduler._recover_stuck_tasks()

    unchanged = await repo.get_task("fine")
    assert unchanged.status == TaskStatus.SCHEDULED


# --- executions + no Supabase error logs --------------------------------------

async def test_no_supabase_error_logs_when_unconfigured(repo_module, caplog):
    import logging

    # The one-time "credentials missing" notice on construction is expected;
    # what must NOT happen is a per-operation "Supabase X failed" error.
    repo = repo_module.TaskRepository()

    caplog.set_level(logging.ERROR)
    task = _make_task("quiet", next_run=datetime.now())
    await repo.create_task(task)
    await repo.update_task(task)
    await repo.get_due_tasks(current_time=datetime.now())
    repo.delete_task("quiet")

    supabase_op_errors = [r for r in caplog.records if "Supabase" in r.getMessage() and "failed" in r.getMessage()]
    assert supabase_op_errors == []


async def test_delete_task_removes_task(repo_module):
    repo = repo_module.TaskRepository()
    task = _make_task("gone", next_run=datetime.now())
    await repo.create_task(task)
    assert repo.delete_task("gone") is True
    assert await repo.get_task("gone") is None
