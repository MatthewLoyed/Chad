"""
Local-first parity tests for StateRepository (#493 tech debt).

`system_state` (used e.g. by browser-history incremental sync watermarks) previously had
no `LocalPostgrestShim` registration, so every verb silently no-op'd when SUPABASE_URL
wasn't configured -- a sync cursor written with set_value() was never actually persisted,
and incremental sync fell back to its 24h default window on every restart. These tests
confirm set/get round-trip through the local SQLite store and survive a simulated restart.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_store, local_postgrest


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    """Force SupabaseManager into its disabled (local-shim) path over an isolated DB file."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    db = tmp_path / "state_local.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)

    import importlib
    from src.infrastructure.repositories import state_repository as repo_mod
    importlib.reload(repo_mod)

    return repo_mod


def _restart_manager(monkeypatch):
    """Simulate a Brain restart: drop the SupabaseManager singleton, keep the DB file."""
    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)


def test_set_and_get_value_local(local_env):
    repo_mod = local_env
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    supabase = get_supabase_manager()
    assert supabase.enabled is False

    repo = repo_mod.StateRepository()
    assert repo.set_value("browser_history_sync_cursor", {"last_visit_time": 123456}) is True
    assert repo.get_value("browser_history_sync_cursor") == {"last_visit_time": 123456}


def test_update_existing_value_local(local_env):
    repo_mod = local_env
    repo = repo_mod.StateRepository()

    repo.set_value("cursor", {"n": 1})
    repo.set_value("cursor", {"n": 2})
    assert repo.get_value("cursor") == {"n": 2}


def test_watermark_survives_restart(local_env, monkeypatch):
    """A sync watermark written before a simulated Brain restart is still readable after --
    this is the exact bug reported in #493 (incremental sync resetting to a 24h window)."""
    repo_mod = local_env
    repo = repo_mod.StateRepository()

    repo.set_value("browser_history_sync_cursor", {"last_visit_time": 987654})

    _restart_manager(monkeypatch)

    from src.infrastructure.database.supabase_manager import get_supabase_manager
    supabase_after = get_supabase_manager()
    assert supabase_after.enabled is False

    repo_after = repo_mod.StateRepository()
    assert repo_after.get_value("browser_history_sync_cursor") == {"last_visit_time": 987654}


def test_get_value_missing_key_returns_default(local_env):
    repo_mod = local_env
    repo = repo_mod.StateRepository()

    assert repo.get_value("does_not_exist", default="fallback") == "fallback"
