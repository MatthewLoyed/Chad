"""Characterization tests for MemoryRepository's local (sqlite) backend.

These lock the observable behavior of the zero-config local path (issue #416) so the
backend-strategy refactor can be proven behavior-preserving. Every method is exercised
end-to-end against a real temp sqlite DB (via CHAD_LOCAL_DB_PATH), with Supabase disabled.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.repositories.memory_repository import MemoryRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A MemoryRepository wired to a fresh temp sqlite DB with Supabase disabled."""
    from src.infrastructure.database import local_store

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "mem.db"))
    # local_store caches the resolved path in a module global; reset it so each test
    # gets its own isolated temp DB rather than reusing the first test's path.
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)

    fake_supabase = MagicMock()
    fake_supabase.enabled = False
    with patch(
        "src.infrastructure.repositories.memory_repository.get_supabase_manager",
        return_value=fake_supabase,
    ):
        yield MemoryRepository()


class TestMemoryCrud:
    def test_create_and_get(self, repo):
        mid = repo.create_memory("hello world", category="note", importance=0.8, source="user")
        assert mid > 0
        got = repo.get_memory(mid)
        assert got is not None
        assert got["content"] == "hello world"
        assert got["category"] == "note"
        assert got["metadata"] == {}

    def test_update(self, repo):
        mid = repo.create_memory("original")
        assert repo.update_memory(mid, content="updated", importance=0.9) is True
        got = repo.get_memory(mid)
        assert got["content"] == "updated"
        assert got["importance"] == 0.9

    def test_delete(self, repo):
        mid = repo.create_memory("temp")
        assert repo.delete_memory(mid) is True
        assert repo.get_memory(mid) is None

    def test_get_missing_returns_none(self, repo):
        assert repo.get_memory(999999) is None

    def test_clear_all(self, repo):
        repo.create_memory("a")
        repo.create_memory("b")
        assert repo.clear_all() is True
        assert repo.list_memories() == []


class TestMemoryQueries:
    def test_list_filters_by_category(self, repo):
        repo.create_memory("x", category="work")
        repo.create_memory("y", category="home")
        work = repo.list_memories(category="work")
        assert len(work) == 1 and work[0]["category"] == "work"

    def test_search_matches_substring(self, repo):
        repo.create_memory("buy milk and eggs")
        repo.create_memory("call the dentist")
        res = repo.search_memories("milk")
        assert len(res) == 1 and "milk" in res[0]["content"]

    def test_has_memory_with_hash(self, repo):
        repo.create_memory("hashed", metadata={"hash": "abc123"})
        assert repo.has_memory_with_hash("abc123") is True
        assert repo.has_memory_with_hash("nope") is False
        assert repo.has_memory_with_hash("") is False

    def test_vector_search_ranks_by_similarity(self, repo):
        repo.create_memory("near", embedding=[1.0, 0.0, 0.0])
        repo.create_memory("far", embedding=[0.0, 1.0, 0.0])
        res = repo.vector_search_memories([1.0, 0.0, 0.0], match_count=1, match_threshold=0.1)
        assert len(res) == 1 and res[0]["content"] == "near"

    def test_search_by_category_scopes_to_category_and_query(self, repo):
        repo.create_memory("visited target page", category="personal_index")
        repo.create_memory("visited other page", category="personal_index")
        repo.create_memory("target but wrong category", category="general")
        res = repo.search_memories_by_category("target", category="personal_index")
        assert len(res) == 1 and res[0]["content"] == "visited target page"

    def test_get_all_long_term_sorted_by_importance(self, repo):
        repo.create_memory("low", importance=0.2)
        repo.create_memory("high", importance=0.95)
        res = repo.get_all_long_term_memories()
        assert res[0]["content"] == "high"

    def test_expire_by_source(self, repo):
        repo.create_memory("old", source="chrome")
        # max_age_days=0 → cutoff is now; the just-created row is < cutoff, so it expires.
        assert repo.expire_memories_by_source("chrome", max_age_days=0) is True


class TestHashIndex:
    def test_has_memory_with_hash_uses_index_not_full_scan(self, repo):
        # Regression guard for issue #601: has_memory_with_hash used to be an
        # unindexed `SCAN memories` computing json_extract per row -- 38ms/call at
        # ~11k rows in production and growing linearly with table size, dominating
        # bulk-ingestion sync cycles. Assert the query plan uses the expression
        # index (idx_memories_hash) instead of scanning every row.
        from src.infrastructure.database import local_store

        conn = local_store.local_db_connect()
        try:
            plan_rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM memories "
                "WHERE json_extract(metadata, '$.hash') = ? LIMIT 1",
                ("x",),
            ).fetchall()
            plan_text = " ".join(str(r["detail"]) for r in plan_rows)
            assert "idx_memories_hash" in plan_text
            assert "SCAN memories" not in plan_text
        finally:
            conn.close()

    def test_message_id_lookup_uses_index(self, repo):
        # Issue #617: Gmail upsert find_by_message_id should hit the expression index.
        from src.infrastructure.database import local_store

        conn = local_store.local_db_connect()
        try:
            plan_rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM memories "
                "WHERE category = ? AND json_extract(metadata, '$.message_id') = ? LIMIT 1",
                ("personal_index", "x"),
            ).fetchall()
            plan_text = " ".join(str(r["detail"]) for r in plan_rows)
            assert "idx_memories_message_id" in plan_text
        finally:
            conn.close()


class TestSessionIsolation:
    def test_list_scopes_to_session_and_global(self, repo):
        repo.create_memory("global")  # session_key None
        repo.create_memory("mine", session_key="discord:acct1:chan")
        repo.create_memory("theirs", session_key="discord:acct2:chan")
        res = repo.list_memories(session_key="discord:acct1:chan")
        contents = {m["content"] for m in res}
        assert "mine" in contents and "global" in contents
        assert "theirs" not in contents


class TestHistoryAndStats:
    def test_message_roundtrip(self, repo):
        mid = repo.create_message("user", "hi", session_key="s1")
        assert mid > 0
        hist = repo.get_conversation_history(session_key="s1")
        assert len(hist) == 1 and hist[0]["content"] == "hi"
        assert repo.count_messages() == 1

    def test_clear_history(self, repo):
        repo.create_message("user", "hi")
        assert repo.clear_conversation_history() is True
        assert repo.count_messages() == 0

    def test_statistics_local(self, repo):
        repo.create_memory("m", category="work")
        repo.create_message("user", "hi")
        stats = repo.get_statistics()
        assert stats["total_memories"] == 1
        assert stats["total_messages"] == 1
        assert stats["categories"].get("work") == 1
        assert stats["remote"] is False
