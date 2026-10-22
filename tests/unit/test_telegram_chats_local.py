"""
Local-first parity test for TelegramChatRepository (issue #471, part of #462).

Telegram transport (bot API + webhook) always needs the cloud — this test
proves only what's claimed to be local: with no `SUPABASE_URL` configured, the
chat_id <-> session_key routing state lives in the SQLite shim and survives a
process restart (simulated by tearing down and rebuilding the singletons that
would be re-created on a fresh Brain boot).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_store, local_postgrest
from src.infrastructure.database import supabase_manager as sm_mod
from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository


@pytest.fixture
def offline_env(tmp_path, monkeypatch):
    """No Supabase credentials, an isolated on-disk sqlite file, fresh singletons."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    db = tmp_path / "telegram_local.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)
    return db


def _restart_brain(monkeypatch):
    """Simulate a Brain restart: tear down the shim + manager singletons, keep the db file."""
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)


def test_supabase_manager_is_disabled_offline(offline_env):
    mgr = sm_mod.SupabaseManager()
    assert mgr.enabled is False


def test_routing_state_resolves_offline(offline_env, monkeypatch):
    repo = TelegramChatRepository()
    session_key = repo.create_or_update_chat(
        chat_id=111, user_id=222, username="matt", first_name="Matt"
    )

    assert repo.get_session_key(111) == session_key
    assert repo.get_chat_id(session_key) == 111

    chat = repo.get_chat(111)
    assert chat is not None
    assert chat.chat_id == 111
    assert chat.user_id == 222
    assert chat.is_active


def test_routing_state_survives_brain_restart(offline_env, monkeypatch):
    repo = TelegramChatRepository()
    session_key = repo.create_or_update_chat(chat_id=333, user_id=444, username="sam")

    _restart_brain(monkeypatch)

    repo_after_restart = TelegramChatRepository()
    assert repo_after_restart.get_session_key(333) == session_key
    assert repo_after_restart.get_chat_id(session_key) == 333


def test_create_or_update_is_idempotent_on_existing_chat(offline_env):
    repo = TelegramChatRepository()
    first_key = repo.create_or_update_chat(chat_id=555, user_id=666, username="old_name")
    second_key = repo.create_or_update_chat(chat_id=555, user_id=666, username="new_name")

    assert first_key == second_key
    chat = repo.get_chat(555)
    assert chat.username == "new_name"


def test_active_chats_and_deactivation(offline_env):
    repo = TelegramChatRepository()
    repo.create_or_update_chat(chat_id=1, user_id=10)
    repo.create_or_update_chat(chat_id=2, user_id=20)

    active = repo.get_active_chats()
    assert {c.chat_id for c in active} == {1, 2}
    assert repo.get_default_chat_id() in (1, 2)

    repo.deactivate_chat(1)
    active = repo.get_active_chats()
    assert {c.chat_id for c in active} == {2}


def test_get_stats(offline_env):
    repo = TelegramChatRepository()
    repo.create_or_update_chat(chat_id=1, user_id=10)
    repo.create_or_update_chat(chat_id=2, user_id=10)
    repo.deactivate_chat(2)

    stats = repo.get_stats()
    assert stats["total_chats"] == 2
    assert stats["active_chats"] == 1
    assert stats["unique_users"] == 1
