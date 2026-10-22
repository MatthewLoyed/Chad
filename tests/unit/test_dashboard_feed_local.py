"""
Local-shim parity tests for dashboard_feed.py (issue #469, part of #462).

Runs the whole append/list/dismiss/run-action lifecycle with no SUPABASE_URL
configured, so every call routes through LocalPostgrestShim, and confirms
entries survive a fresh SupabaseManager (simulating a Brain restart).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_store, local_postgrest
from src.core.intelligence import dashboard_feed


@pytest.fixture(autouse=True)
def local_only(tmp_path, monkeypatch):
    """Force the disabled (no-Supabase) path onto an isolated on-disk sqlite file."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    db = tmp_path / "dashboard_test.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    # dashboard_feed registered its schema against whatever shim instance was
    # live at import time; re-register against this test's fresh shim.
    dashboard_feed.register_table(
        "dashboard_entries",
        columns={
            "kind": "TEXT", "title": "TEXT", "body": "TEXT",
            "metadata": "JSON", "action": "JSON", "status": "TEXT",
            "pinned": "INTEGER", "expires_at": "TEXT",
            "created_at": "TEXT", "updated_at": "TEXT",
        },
        pk="id",
    )
    yield


async def test_append_and_get_feed_orders_pinned_first():
    await dashboard_feed.append_entry("status", "system ok", "all green")
    await dashboard_feed.append_entry("life", "wedding countdown", "300 days", pinned=True)

    feed = await dashboard_feed.get_feed()
    assert [e["title"] for e in feed] == ["wedding countdown", "system ok"]
    assert feed[0]["pinned"] in (True, 1)


async def test_get_feed_excludes_dismissed_and_non_active():
    entry = await dashboard_feed.append_entry("thought", "note", "body")
    assert await dashboard_feed.set_entry_status(str(entry["id"]), "dismissed")

    feed = await dashboard_feed.get_feed()
    assert feed == []


async def test_get_feed_excludes_expired_pinned():
    """Expired pinned alerts must leave the feed (not linger via neq.dismissed)."""
    entry = await dashboard_feed.append_entry(
        "status", "Auto-update failed", "dirty tree",
        metadata={"source": "auto_update"}, pinned=True, expires_in_hours=0.0001,
    )
    # Force expiry without waiting — set expires_at in the past and re-read.
    from src.infrastructure.database.supabase_manager import get_supabase_manager
    supabase = get_supabase_manager()
    await supabase.async_update(
        "dashboard_entries",
        {"id": f"eq.{entry['id']}"},
        {"expires_at": "2000-01-01T00:00:00Z"},
    )
    feed = await dashboard_feed.get_feed()
    assert all(str(e.get("id")) != str(entry["id"]) for e in feed)


async def test_dismiss_by_metadata_source_clears_matching_only():
    await dashboard_feed.append_entry(
        "status", "Auto-update failed", "err1",
        metadata={"source": "auto_update"}, pinned=True,
    )
    await dashboard_feed.append_entry(
        "status", "Auto-update failed", "err2",
        metadata={"source": "auto_update"}, pinned=True,
    )
    await dashboard_feed.append_entry("life", "wedding", "soon", pinned=True)

    n = await dashboard_feed.dismiss_by_metadata_source("auto_update")
    assert n == 2
    feed = await dashboard_feed.get_feed()
    assert [e["title"] for e in feed] == ["wedding"]


async def test_get_feed_collapses_duplicate_auto_update_alerts():
    await dashboard_feed.append_entry(
        "status", "Auto-update failed", "older",
        metadata={"source": "auto_update"}, pinned=True,
    )
    newest = await dashboard_feed.append_entry(
        "status", "Auto-update failed", "newer",
        metadata={"source": "auto_update"}, pinned=True,
    )
    feed = await dashboard_feed.get_feed()
    auto = [e for e in feed if (e.get("metadata") or {}).get("source") == "auto_update"]
    assert len(auto) == 1
    assert str(auto[0]["id"]) == str(newest["id"])
    assert auto[0]["body"] == "newer"


async def test_rejects_invalid_kind():
    assert await dashboard_feed.append_entry("bogus", "t", "b") is None


async def test_run_entry_action_executes_tool_and_marks_done():
    entry = await dashboard_feed.append_entry(
        "action", "run me", "body",
        action={"type": "tool", "target": "some_tool", "params": {"x": 1}},
    )

    class FakeToolRegistry:
        async def execute_tool(self, name, params):
            assert name == "some_tool"
            assert params == {"x": 1}
            return {"ok": True}

    result = await dashboard_feed.run_entry_action(str(entry["id"]), FakeToolRegistry())
    assert result == {"success": True, "result": {"ok": True}}

    updated = await dashboard_feed.get_entry_by_id(str(entry["id"]))
    assert updated["status"] == "done"


async def test_run_entry_action_missing_entry():
    result = await dashboard_feed.run_entry_action("999", tool_registry=object())
    assert result["success"] is False


async def test_entries_survive_simulated_brain_restart(monkeypatch):
    """A fresh SupabaseManager (new process) must still see rows written earlier."""
    entry = await dashboard_feed.append_entry("discovery", "found it", "details")

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    restarted = await dashboard_feed.get_entry_by_id(str(entry["id"]))
    assert restarted is not None
    assert restarted["title"] == "found it"


async def test_no_supabase_error_logs_when_unconfigured(caplog):
    with caplog.at_level("ERROR"):
        await dashboard_feed.append_entry("thought", "quiet", "body")
        await dashboard_feed.get_feed()
    assert not any("Supabase" in r.message for r in caplog.records)
