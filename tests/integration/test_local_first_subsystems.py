"""
Local-first integration test (issue #472, Slice H of epic #462).

Unlike the per-subsystem unit tests (tests/unit/test_*_local.py), which each
run against their own isolated sqlite file, this test points *all six*
local-first subsystems — cron/agent_tasks, reflection lessons, kanban,
workflows, dashboard feed, and OATS — at the same on-disk sqlite file with
no SUPABASE_URL configured, and proves they coexist (no table/registration
collisions) and each survives a simulated Brain restart (a fresh repo/module
instance reconnecting to the same file still sees prior writes).
"""

import importlib
import sys
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_store, local_postgrest


@pytest.fixture
def local_first_env(tmp_path, monkeypatch):
    """Force every subsystem into its disabled-Supabase (local-shim) path over
    one shared sqlite file, mirroring a real no-Supabase install."""
    db = tmp_path / "local_first_all.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    # task_repository and dashboard_feed both register their table at
    # module-import time, so reload them against the fresh shim instance
    # created above (they may already be cached from an earlier test module
    # in this same pytest session, pointing at a stale/torn-down shim).
    import src.infrastructure.repositories.task_repository as task_repo_mod
    import src.core.intelligence.dashboard_feed as dashboard_feed

    from src.infrastructure.repositories.kanban_repository import KanbanRepository
    from src.infrastructure.repositories.workflow_repository import WorkflowRepository
    from src.core.intelligence import reflection_lessons_store as reflection_store
    from src.core.orchestration.oats_refiner import ensure_local_oats_tables

    # Some of the above imports transitively import ConfigManager, whose
    # module-level load_dotenv(override=True) re-populates SUPABASE_URL/
    # SUPABASE_ANON_KEY from the real .env file. Delete them again now, after
    # imports have settled, right before constructing the SupabaseManager
    # singleton so it actually sees an unconfigured environment.
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)

    importlib.reload(task_repo_mod)
    importlib.reload(dashboard_feed)
    reflection_store._register_local_table()
    ensure_local_oats_tables()

    assert sm_mod.get_supabase_manager().enabled is False

    return {
        "task_repo_mod": task_repo_mod,
        "kanban_cls": KanbanRepository,
        "workflow_cls": WorkflowRepository,
        "reflection_store": reflection_store,
        "dashboard_feed": dashboard_feed,
    }


def _restart(monkeypatch):
    """Simulate a Brain restart: drop the SupabaseManager singleton, keep the
    on-disk sqlite file untouched."""
    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(768).astype(np.float32)
    return v / np.linalg.norm(v)


def _seed_tool(name: str, base: np.ndarray) -> None:
    conn = local_store.local_db_connect()
    try:
        conn.execute(
            "INSERT INTO tool_embeddings (name, description_hash, embedding, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET embedding=excluded.embedding",
            (name, "hash", local_store.embedding_to_blob(base.tolist()), datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


async def test_all_subsystems_coexist_and_survive_restart(local_first_env, monkeypatch):
    env = local_first_env

    # --- cron / agent_tasks ------------------------------------------------
    from src.domain.models import AgentTask, TaskStatus, TaskTrigger

    task_repo_a = env["task_repo_mod"].TaskRepository()
    next_run = datetime.now() + timedelta(minutes=5)
    task = AgentTask(
        task_id="lf-task", name="local-first task", description="d",
        trigger=TaskTrigger.EVERY, schedule="30m",
        status=TaskStatus.SCHEDULED, next_run=next_run,
    )
    assert await task_repo_a.create_task(task) is True

    # --- kanban --------------------------------------------------------------
    kanban_a = env["kanban_cls"]()
    card = await kanban_a.create_card("local-first card", description="d")
    assert card is not None
    card_id = card["id"]

    # --- workflows -------------------------------------------------------------
    workflow_a = env["workflow_cls"]()
    workflow = await workflow_a.create_workflow(
        "local-first workflow", steps=[{"tool_name": "noop", "params": {}, "label": "step 1"}],
    )
    assert workflow is not None
    workflow_id = workflow["id"]

    # --- reflection lessons ----------------------------------------------------
    reflection_store = env["reflection_store"]
    from src.infrastructure.database.supabase_manager import get_supabase_manager
    supabase = get_supabase_manager()
    assert await reflection_store.persist_lesson_and_publish(
        supabase, {"scope": "tools", "lesson": "local-first lesson", "confidence": 0.9}, "lesson",
    ) is True
    pending = await reflection_store.get_pending_lessons()
    lesson_id = pending[0]["id"]

    # --- dashboard feed --------------------------------------------------------
    dashboard_feed = env["dashboard_feed"]
    entry = await dashboard_feed.append_entry("status", "local-first entry", "body")
    entry_id = str(entry["id"])

    # --- OATS --------------------------------------------------------------
    from scripts.oats_refine import refine_embeddings

    base = _unit(1)
    _seed_tool("local_first_tool", base)
    success_dir = _unit(2)
    for _ in range(12):
        supabase.insert("tool_outcome_log", {
            "tool_name": "local_first_tool", "query_text": "q",
            "query_embedding": success_dir.tolist(), "outcome": "success",
        })
    assert refine_embeddings(min_outcomes=10) is True

    # --- simulate a Brain restart: fresh SupabaseManager, same sqlite file ---
    _restart(monkeypatch)

    task_repo_b = env["task_repo_mod"].TaskRepository()
    reloaded_task = await task_repo_b.get_task("lf-task")
    assert reloaded_task is not None
    assert reloaded_task.status == TaskStatus.SCHEDULED
    assert reloaded_task.next_run == next_run

    kanban_b = env["kanban_cls"]()
    reloaded_card = await kanban_b.get_card(card_id)
    assert reloaded_card is not None
    assert reloaded_card["title"] == "local-first card"

    workflow_b = env["workflow_cls"]()
    reloaded_workflow = await workflow_b.get_workflow(workflow_id)
    assert reloaded_workflow is not None
    assert reloaded_workflow["name"] == "local-first workflow"

    reloaded_lesson = await reflection_store.get_lesson_by_id(lesson_id)
    assert reloaded_lesson is not None
    assert reloaded_lesson["text"] == "local-first lesson"

    reloaded_entry = await dashboard_feed.get_entry_by_id(entry_id)
    assert reloaded_entry is not None
    assert reloaded_entry["title"] == "local-first entry"

    conn = local_store.local_db_connect()
    try:
        row = conn.execute(
            "SELECT embedding_refined FROM tool_embeddings WHERE name = ?",
            ("local_first_tool",),
        ).fetchone()
    finally:
        conn.close()
    assert row["embedding_refined"] is not None
