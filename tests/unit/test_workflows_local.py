"""
Local-parity tests for WorkflowRepository (issue #468).

With no SUPABASE_URL, workflow CRUD routes through LocalPostgrestShim and
persists in the local sqlite store.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_postgrest, local_store
from src.infrastructure.database.supabase_manager import SupabaseManager
from src.infrastructure.repositories.workflow_repository import WorkflowRepository


@pytest.fixture
def local_workflows(tmp_path, monkeypatch):
    """Isolated local sqlite + disabled SupabaseManager for workflow tests."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    db = tmp_path / "workflows_local.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)
    monkeypatch.setattr(SupabaseManager, "_instance", None, raising=False)

    import src.infrastructure.database.supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    yield WorkflowRepository()


def _reset_singletons(monkeypatch, db_path):
    """Simulate a Brain restart: fresh manager + shim, same on-disk db."""
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)
    monkeypatch.setattr(SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db_path))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)


SAMPLE_STEPS = [
    {"tool_name": "get_datetime", "params": {}, "label": "Check time"},
    {"tool_name": "web_search", "params": {"queries": ["news"]}, "label": "Search"},
]


@pytest.mark.asyncio
async def test_create_list_get_workflow(local_workflows):
    repo = local_workflows
    created = await repo.create_workflow(
        name="Morning routine",
        steps=SAMPLE_STEPS,
        description="Start-of-day checks",
    )
    assert created is not None
    assert created["name"] == "Morning routine"
    assert created["steps"] == SAMPLE_STEPS

    workflows = await repo.list_workflows()
    assert len(workflows) == 1
    assert workflows[0]["name"] == "Morning routine"

    workflow_id = str(created["id"])
    got = await repo.get_workflow(workflow_id)
    assert got is not None
    assert got["steps"] == SAMPLE_STEPS
    assert got["description"] == "Start-of-day checks"


@pytest.mark.asyncio
async def test_update_and_delete_workflow(local_workflows):
    repo = local_workflows
    created = await repo.create_workflow(name="Old name", steps=SAMPLE_STEPS)
    workflow_id = str(created["id"])

    new_steps = [{"tool_name": "get_datetime", "params": {}, "label": "Time"}]
    assert await repo.update_workflow(
        workflow_id, name="New name", description="Updated", steps=new_steps,
    ) is True

    updated = await repo.get_workflow(workflow_id)
    assert updated["name"] == "New name"
    assert updated["description"] == "Updated"
    assert updated["steps"] == new_steps

    assert repo.delete_workflow(workflow_id) is True
    assert await repo.get_workflow(workflow_id) is None


@pytest.mark.asyncio
async def test_get_workflow_for_run(local_workflows):
    """Run path reads steps via get_workflow — verify local round-trip."""
    repo = local_workflows
    created = await repo.create_workflow(name="Runnable", steps=SAMPLE_STEPS)
    workflow_id = str(created["id"])

    loaded = await repo.get_workflow(workflow_id)
    assert loaded is not None
    assert len(loaded["steps"]) == 2
    assert loaded["steps"][0]["tool_name"] == "get_datetime"


@pytest.mark.asyncio
async def test_workflows_survive_brain_restart(local_workflows, tmp_path, monkeypatch):
    repo = local_workflows
    created = await repo.create_workflow(name="Persist me", steps=SAMPLE_STEPS)
    workflow_id = str(created["id"])
    db_path = tmp_path / "workflows_local.db"

    _reset_singletons(monkeypatch, db_path)
    repo2 = WorkflowRepository()
    got = await repo2.get_workflow(workflow_id)
    assert got is not None
    assert got["name"] == "Persist me"
    assert got["steps"] == SAMPLE_STEPS
