"""
Local-parity tests for KanbanRepository (issue #468).

With no SUPABASE_URL, kanban CRUD routes through LocalPostgrestShim and
persists in the local sqlite store.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_postgrest, local_store
from src.infrastructure.database.supabase_manager import SupabaseManager
from src.infrastructure.repositories.kanban_repository import KanbanRepository
from src.domain.work_status import DEFAULT_STATUS


@pytest.fixture
def local_kanban(tmp_path, monkeypatch):
    """Isolated local sqlite + disabled SupabaseManager for kanban tests."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    db = tmp_path / "kanban_local.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)
    monkeypatch.setattr(SupabaseManager, "_instance", None, raising=False)

    import src.infrastructure.database.supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    yield KanbanRepository()


def _reset_singletons(monkeypatch, db_path):
    """Simulate a Brain restart: fresh manager + shim, same on-disk db."""
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)
    monkeypatch.setattr(SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db_path))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)


@pytest.mark.asyncio
async def test_create_list_get_card(local_kanban):
    repo = local_kanban
    created = await repo.create_card("Buy milk", description="2%")
    assert created is not None
    assert created["title"] == "Buy milk"
    assert created["description"] == "2%"

    cards = await repo.list_cards()
    assert len(cards) == 1
    assert cards[0]["title"] == "Buy milk"

    card_id = str(created["id"])
    got = await repo.get_card(card_id)
    assert got is not None
    assert got["title"] == "Buy milk"


@pytest.mark.asyncio
async def test_list_cards_filtered_by_column(local_kanban):
    repo = local_kanban
    await repo.create_card("Todo item", column_id="todo")
    await repo.create_card("Done item", column_id="done")

    todo_cards = await repo.list_cards(column_id="todo")
    assert len(todo_cards) == 1
    assert todo_cards[0]["title"] == "Todo item"


@pytest.mark.asyncio
async def test_move_card_valid_transition(local_kanban):
    repo = local_kanban
    created = await repo.create_card("Task", column_id=DEFAULT_STATUS.value)
    card_id = str(created["id"])

    assert await repo.move_card(card_id, "in_progress", order_index=1) is True
    moved = await repo.get_card(card_id)
    assert moved["column_id"] == "in_progress"
    assert moved["order_index"] == 1


@pytest.mark.asyncio
async def test_move_card_invalid_transition_raises(local_kanban):
    repo = local_kanban
    created = await repo.create_card("Task", column_id="todo")
    card_id = str(created["id"])

    with pytest.raises(ValueError, match="Cannot move"):
        await repo.move_card(card_id, "done")


@pytest.mark.asyncio
async def test_update_and_delete_card(local_kanban):
    repo = local_kanban
    created = await repo.create_card("Old title")
    card_id = str(created["id"])

    assert await repo.update_card(card_id, title="New title") is True
    updated = await repo.get_card(card_id)
    assert updated["title"] == "New title"

    assert repo.delete_card(card_id) is True
    assert await repo.get_card(card_id) is None


@pytest.mark.asyncio
async def test_kanban_survives_brain_restart(local_kanban, tmp_path, monkeypatch):
    repo = local_kanban
    created = await repo.create_card("Persist me")
    card_id = str(created["id"])
    db_path = tmp_path / "kanban_local.db"

    _reset_singletons(monkeypatch, db_path)
    repo2 = KanbanRepository()
    got = await repo2.get_card(card_id)
    assert got is not None
    assert got["title"] == "Persist me"
