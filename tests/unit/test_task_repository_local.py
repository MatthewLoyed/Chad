"""
Local-first characterization tests for TaskRepository (#521 audit follow-up).

TaskRepository never hand-branched local vs. cloud: every method calls
``self.supabase.async_*``/``self.supabase.*`` and lets ``SupabaseManager`` route to the
``LocalPostgrestShim`` when ``SUPABASE_URL`` isn't configured (the single local/cloud
branch lives in ``SupabaseManager``, not here). It was covered only by the shared
integration test (``tests/integration/test_local_first_subsystems.py``); this adds a
focused unit-level characterization of the local (sqlite-shim) path so the transparent
delegation contract can't silently regress.

Mirrors ``test_state_repository_local.py``'s fixture idiom.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_store, local_postgrest


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    """Force SupabaseManager into its disabled (local-shim) path over an isolated DB file."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    db = tmp_path / "task_local.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)

    import importlib
    # agent_tasks/task_executions are registered at module import, so reload the module
    # against the fresh shim instance created above.
    from src.infrastructure.repositories import task_repository as repo_mod
    importlib.reload(repo_mod)

    return repo_mod


def _restart_manager(monkeypatch):
    """Simulate a Brain restart: drop the SupabaseManager singleton, keep the DB file."""
    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)


def _make_task(task_id: str = "t1", *, status=None, name: str = "Nightly report"):
    from src.domain.models import AgentTask, TaskStatus, TaskTrigger
    return AgentTask(
        task_id=task_id,
        name=name,
        description="",
        trigger=TaskTrigger.CRON,
        status=status or TaskStatus.SCHEDULED,
        schedule="0 9 * * *",
        tool_executions=[],
        created_at=datetime.now(),
        last_run=None,
        next_run=datetime.now() + timedelta(hours=1),
        metadata={"k": "v"},
    )


@pytest.mark.asyncio
async def test_create_and_get_task_local(local_env):
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    assert get_supabase_manager().enabled is False

    repo = local_env.TaskRepository()
    assert await repo.create_task(_make_task("t1")) is True

    got = await repo.get_task("t1")
    assert got is not None
    assert got.task_id == "t1"
    assert got.name == "Nightly report"
    assert got.metadata == {"k": "v"}


@pytest.mark.asyncio
async def test_update_task_local(local_env):
    from src.domain.models import TaskStatus

    repo = local_env.TaskRepository()
    await repo.create_task(_make_task("t2", name="original"))

    task = await repo.get_task("t2")
    task.name = "renamed"
    task.status = TaskStatus.RUNNING
    assert await repo.update_task(task) is True

    got = await repo.get_task("t2")
    assert got.name == "renamed"
    assert got.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_list_and_filter_tasks_local(local_env):
    from src.domain.models import TaskStatus

    repo = local_env.TaskRepository()
    await repo.create_task(_make_task("sched", status=TaskStatus.SCHEDULED))
    await repo.create_task(_make_task("run", status=TaskStatus.RUNNING))

    all_tasks = await repo.get_all_tasks()
    assert {t.task_id for t in all_tasks} == {"sched", "run"}

    scheduled = await repo.list_tasks(status=TaskStatus.SCHEDULED)
    assert [t.task_id for t in scheduled] == ["sched"]


@pytest.mark.asyncio
async def test_get_due_tasks_local(local_env):
    from src.domain.models import AgentTask, TaskStatus, TaskTrigger

    repo = local_env.TaskRepository()
    due = AgentTask(
        task_id="due",
        name="due task",
        description="",
        trigger=TaskTrigger.CRON,
        status=TaskStatus.SCHEDULED,
        schedule="* * * * *",
        tool_executions=[],
        created_at=datetime.now(),
        last_run=None,
        next_run=datetime.now() - timedelta(minutes=5),  # already past → due
        metadata={},
    )
    await repo.create_task(due)

    result = await repo.get_due_tasks()
    assert [t.task_id for t in result] == ["due"]


@pytest.mark.asyncio
async def test_delete_task_local(local_env):
    repo = local_env.TaskRepository()
    await repo.create_task(_make_task("gone"))
    assert repo.delete_task("gone") is True
    assert await repo.get_task("gone") is None


@pytest.mark.asyncio
async def test_task_survives_restart_local(local_env, monkeypatch):
    """A task written before a simulated Brain restart is still readable afterwards."""
    repo = local_env.TaskRepository()
    await repo.create_task(_make_task("persist"))

    _restart_manager(monkeypatch)

    from src.infrastructure.database.supabase_manager import get_supabase_manager
    assert get_supabase_manager().enabled is False

    repo_after = local_env.TaskRepository()
    got = await repo_after.get_task("persist")
    assert got is not None and got.task_id == "persist"
