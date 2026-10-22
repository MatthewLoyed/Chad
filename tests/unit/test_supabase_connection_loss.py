"""Supabase connection-loss-mid-turn coverage for issue #216.

test_local_postgrest.py already covers the two boot-time paths: Supabase
unconfigured -> shim, Supabase configured -> shim untouched. This covers the
remaining gap: Supabase IS configured (enabled=True) but the connection is
lost mid-operation (network drop, DNS failure, timeout). Every verb must
degrade to its safe fallback value instead of raising, so a turn continues
gracefully instead of crashing when Supabase becomes unreachable mid-turn.
"""
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture
def enabled_manager(monkeypatch):
    from src.infrastructure.database import supabase_manager as sm_mod

    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    mgr = sm_mod.SupabaseManager()
    assert mgr.enabled is True
    mgr._client = MagicMock()
    # This suite isolates transport fallback. Real local table registration and
    # persistence are exercised by test_local_postgrest.py. A random unregistered
    # table is not a valid local backend fixture.
    local = MagicMock()
    local.query.return_value = []
    local.insert.return_value = None
    local.upsert.return_value = None
    local.update.return_value = False
    local.delete.return_value = False
    local.rpc.return_value = None
    monkeypatch.setattr(mgr, "_local", lambda: local)
    yield mgr
    assert local.method_calls, "A failed remote operation must reach the local fallback"


def test_query_degrades_to_empty_list_on_connection_loss(enabled_manager):
    enabled_manager._client.get.side_effect = httpx.ConnectError("connection lost mid-turn")
    assert enabled_manager.query("notes") == []


def test_insert_degrades_to_none_on_connection_loss(enabled_manager):
    enabled_manager._client.post.side_effect = httpx.ConnectError("connection lost mid-turn")
    assert enabled_manager.insert("notes", {"body": "hi"}) is None


def test_upsert_degrades_to_none_on_connection_loss(enabled_manager):
    enabled_manager._client.post.side_effect = httpx.ConnectError("connection lost mid-turn")
    assert enabled_manager.upsert("notes", {"body": "hi"}) is None


def test_update_degrades_to_false_on_connection_loss(enabled_manager):
    enabled_manager._client.patch.side_effect = httpx.ConnectError("connection lost mid-turn")
    assert enabled_manager.update("notes", {"id": "eq.1"}, {"body": "hi"}) is False


def test_delete_degrades_to_false_on_connection_loss(enabled_manager):
    enabled_manager._client.delete.side_effect = httpx.ConnectError("connection lost mid-turn")
    assert enabled_manager.delete("notes", {"id": "eq.1"}) is False


def test_rpc_degrades_to_none_on_connection_loss(enabled_manager):
    enabled_manager._client.post.side_effect = httpx.ConnectError("connection lost mid-turn")
    assert enabled_manager.rpc("some_function", {}) is None


@pytest.mark.asyncio
async def test_async_query_degrades_gracefully_too(enabled_manager):
    """The async wrappers (used mid-turn by the orchestration layer) must not propagate either."""
    enabled_manager._client.get.side_effect = httpx.ConnectError("connection lost mid-turn")
    result = await enabled_manager.async_query("notes")
    assert result == []


def test_query_recovers_once_connection_is_restored(enabled_manager):
    """A transient mid-turn outage must not permanently wedge the manager -- the next
    call, once connectivity returns, should succeed normally again."""
    enabled_manager._client.get.side_effect = httpx.ConnectError("connection lost mid-turn")
    assert enabled_manager.query("notes") == []

    fake_response = MagicMock()
    fake_response.json.return_value = [{"id": 1, "body": "back online"}]
    fake_response.raise_for_status.return_value = None
    enabled_manager._client.get.side_effect = None
    enabled_manager._client.get.return_value = fake_response

    assert enabled_manager.query("notes") == [{"id": 1, "body": "back online"}]
