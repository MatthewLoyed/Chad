"""Supabase backup is limited to personal knowledge + persona prompts."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.repositories.memory_repository import MemoryRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from src.infrastructure.database import local_store
    from src.infrastructure.repositories import memory_repository as mr_mod

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "persona_backup.db"))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)

    fake_sb = MagicMock()
    fake_sb.enabled = True
    fake_sb.query = MagicMock(return_value=[])
    fake_sb.insert = MagicMock(return_value={"id": "planner"})
    fake_sb.update = MagicMock(return_value=True)
    monkeypatch.setattr(mr_mod, "get_supabase_manager", lambda: fake_sb)
    # The backup policy imports its factory inside the method as well.
    monkeypatch.setattr(
        "src.infrastructure.database.supabase_manager.get_supabase_manager",
        lambda: fake_sb,
    )

    def fake_get(key, default=None):
        if key == "knowledge_graph.storage":
            return "local_then_supabase"
        if key == "memory.storage":
            return "local"
        return default

    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        fake_get,
    )
    # persona_store writes files under cwd — use tmp overlay
    overlay = tmp_path / "config" / "persona"
    overlay.mkdir(parents=True)
    monkeypatch.setattr(
        "src.infrastructure.repositories.persona_store.overlay_dir",
        lambda: overlay,
    )
    monkeypatch.setattr(
        "src.infrastructure.repositories.persona_store.baseline_dir",
        lambda: tmp_path / "prompts" / "persona",
    )
    (tmp_path / "prompts" / "persona").mkdir(parents=True)

    r = MemoryRepository()
    r.supabase = fake_sb
    return r


def test_persona_write_backs_up_when_graph_mirror_enabled(repo):
    assert repo._persona_backup_enabled() is True
    with patch(
        "src.infrastructure.repositories.persona_store.get_local_shim"
    ) as shim:
        shim.return_value.query.return_value = []
        shim.return_value.insert.return_value = {"id": "1"}
        ok = repo.update_persona_config("planner", "You are careful.", scope="always")
    assert ok is True
    repo.supabase.insert.assert_called()
    args, kwargs = repo.supabase.insert.call_args
    assert args[0] == "persona_config"
    assert args[1]["id"] == "planner"
    assert "careful" in args[1]["content"]


def test_persona_backup_skipped_when_graph_storage_local(tmp_path, monkeypatch):
    from src.infrastructure.database import local_store
    from src.infrastructure.repositories import memory_repository as mr_mod

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "no_backup.db"))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    fake_sb = MagicMock()
    fake_sb.enabled = True
    monkeypatch.setattr(mr_mod, "get_supabase_manager", lambda: fake_sb)
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        lambda key, default=None: (
            "local" if key in ("knowledge_graph.storage", "memory.storage") else default
        ),
    )
    repo = MemoryRepository()
    assert repo._persona_backup_enabled() is False
