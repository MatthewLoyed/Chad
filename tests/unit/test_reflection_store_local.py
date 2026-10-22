"""
Local-first parity tests for reflection_lessons_store (#467, Slice C of #462).

With no SUPABASE_URL configured, `SupabaseManager` routes every verb through
`LocalPostgrestShim` (issue #465) instead of no-op'ing. These tests exercise the
full propose -> queue -> approve/dismiss/answer lifecycle purely against the
local SQLite store, and confirm pending rows survive a process restart
(re-instantiating SupabaseManager against the same on-disk file).
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

    db = tmp_path / "reflection_local.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)

    from src.core.intelligence import reflection_lessons_store as store
    store._register_local_table()

    return store


def _restart_manager(monkeypatch):
    """Simulate a Brain restart: drop the SupabaseManager singleton, keep the DB file."""
    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)


@pytest.mark.asyncio
async def test_propose_to_queue_to_approve_lifecycle(local_env, monkeypatch):
    store = local_env
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    supabase = get_supabase_manager()
    assert supabase.enabled is False

    ok = await store.persist_lesson_and_publish(
        supabase,
        {"scope": "tools", "lesson": "Use web_search for quick lookups", "confidence": 0.9},
        "lesson",
    )
    assert ok is True

    pending = await store.get_pending_lessons()
    assert len(pending) == 1
    row = pending[0]
    assert row["text"] == "Use web_search for quick lookups"
    assert row["status"] == "pending"

    lesson_id = row["id"]
    fetched = await store.get_lesson_by_id(lesson_id)
    assert fetched["text"] == row["text"]

    async def _fake_apply(item):
        return True

    monkeypatch.setattr(store, "apply_lesson", _fake_apply)
    applied = await store.apply_lesson(fetched)
    assert applied is True
    assert await store.set_lesson_status(lesson_id, "applied") is True

    assert await store.get_pending_lessons() == []
    approved = await store.get_approved_lessons()
    assert len(approved) == 1
    assert approved[0]["status"] == "applied"
    assert await store.count_approved_lessons() == 1


@pytest.mark.asyncio
async def test_dismiss_lesson_local(local_env):
    store = local_env
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    supabase = get_supabase_manager()
    await store.persist_lesson_and_publish(
        supabase, {"scope": "user", "lesson": "The user prefers dark mode", "confidence": 0.8}, "lesson",
    )
    row = (await store.get_pending_lessons())[0]

    assert await store.set_lesson_status(row["id"], "dismissed") is True
    assert await store.get_pending_lessons() == []
    # Dismissed rows aren't part of the approved/audit trail either.
    assert await store.get_approved_lessons() == []


@pytest.mark.asyncio
async def test_answer_question_local(local_env, monkeypatch):
    pytest.importorskip("networkx")
    store = local_env
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    supabase = get_supabase_manager()
    await store.persist_lesson_and_publish(
        supabase, {"question": "Which lamp is yours?", "why": "ambiguous device", "confidence": 0.7}, "question",
    )
    row = (await store.get_pending_lessons())[0]
    assert row["kind"] == "question"

    class _FakeGraph:
        async def initialize(self):
            return None

        async def ingest(self, fact, source=None):
            assert "Which lamp is yours?" in fact
            assert "bedroom lamp" in fact
            return True

    monkeypatch.setattr(
        "src.core.intelligence.graph_rag_client.GraphRAGClient", lambda: _FakeGraph()
    )

    ok = await store.answer_question(row["id"], "bedroom lamp")
    assert ok is True

    updated = await store.get_lesson_by_id(row["id"])
    assert updated["status"] == "answered"
    assert updated["answer"] == "bedroom lamp"


@pytest.mark.asyncio
async def test_pending_lessons_survive_restart(local_env, monkeypatch):
    """A pending row written before a simulated Brain restart is still readable after."""
    store = local_env
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    supabase = get_supabase_manager()
    await store.persist_lesson_and_publish(
        supabase, {"scope": "tools", "lesson": "Prefer local tools when offline", "confidence": 0.85}, "lesson",
    )

    _restart_manager(monkeypatch)

    supabase_after = get_supabase_manager()
    assert supabase_after.enabled is False
    pending = await store.get_pending_lessons()
    assert len(pending) == 1
    assert pending[0]["text"] == "Prefer local tools when offline"


@pytest.mark.asyncio
async def test_no_supabase_error_logs_when_unconfigured(local_env, caplog):
    """The store's own logger stays clean; SupabaseManager's one-time startup
    notice ("credentials missing") is expected and logged elsewhere."""
    store = local_env
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    supabase = get_supabase_manager()
    with caplog.at_level("ERROR", logger="ReflectionLessons"):
        await store.persist_lesson_and_publish(
            supabase, {"scope": "tools", "lesson": "No error logs expected", "confidence": 0.6}, "lesson",
        )
        await store.get_pending_lessons()
        await store.get_approved_lessons()
        await store.count_approved_lessons()

    reflection_errors = [r for r in caplog.records if r.name == "ReflectionLessons"]
    assert reflection_errors == []
