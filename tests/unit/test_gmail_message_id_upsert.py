"""Gmail personal_index upsert by message_id (issue #617).

Drive catalog keys on file_id; Gmail must key on message_id so re-syncs update
one row instead of hash-only create/skip. Fixtures only — no live mailbox.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.ingestion.base import BaseIngestor
from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager
from src.infrastructure.repositories.memory_repository import MemoryRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from src.infrastructure.database import local_store

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "mem.db"))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)

    fake_supabase = MagicMock()
    fake_supabase.enabled = False
    with patch(
        "src.infrastructure.repositories.memory_repository.get_supabase_manager",
        return_value=fake_supabase,
    ):
        yield MemoryRepository()


def _gmail_record(
    message_id: str,
    *,
    subject: str = "Budget",
    snippet: str = "Wedding budget draft",
    sender: str = "alice@example.com",
    ts: str = "2026-07-01T12:00:00+00:00",
) -> dict:
    content = (
        f"Email from {sender}: '{subject}'\n"
        f"Snippet: {snippet}"
    )
    ingestor = _FakeGmailIngestor([])
    return {
        "content": content,
        "importance": 0.7,
        "metadata": {
            "source": "gmail",
            "message_id": message_id,
            "sender": sender,
            "subject": subject,
            "timestamp": ts,
            "hash": ingestor.compute_hash(content),
        },
    }


class _FakeGmailIngestor(BaseIngestor):
    def __init__(self, records: list[dict]):
        self._records = records

    def get_source_name(self) -> str:
        return "gmail"

    def ingest(self, last_sync=None):
        return list(self._records)


class TestMessageIdRepository:
    def test_upsert_insert_then_update_same_id(self, repo):
        mid1 = repo.upsert_memory_by_message_id(
            message_id="msg-1",
            content="Email from a: 'Hi'\nSnippet: v1",
            category="personal_index",
            importance=0.7,
            source="gmail",
            embedding=[0.1, 0.2],
            metadata={"message_id": "msg-1", "hash": "h1", "source": "gmail"},
        )
        assert mid1 > 0

        mid2 = repo.upsert_memory_by_message_id(
            message_id="msg-1",
            content="Email from a: 'Hi'\nSnippet: v2 updated",
            category="personal_index",
            importance=0.7,
            source="gmail",
            embedding=[0.3, 0.4],
            metadata={"message_id": "msg-1", "hash": "h2", "source": "gmail"},
        )
        assert mid2 == mid1

        rows = repo.list_memories(category="personal_index")
        assert len(rows) == 1
        assert "v2 updated" in rows[0]["content"]
        assert rows[0]["metadata"]["hash"] == "h2"

    def test_different_message_ids_are_separate_rows(self, repo):
        repo.upsert_memory_by_message_id(
            message_id="msg-a",
            content="a",
            metadata={"message_id": "msg-a", "hash": "ha"},
        )
        repo.upsert_memory_by_message_id(
            message_id="msg-b",
            content="b",
            metadata={"message_id": "msg-b", "hash": "hb"},
        )
        rows = repo.list_memories(category="personal_index")
        assert len(rows) == 2

    def test_find_by_message_id(self, repo):
        repo.upsert_memory_by_message_id(
            message_id="msg-find",
            content="needle",
            metadata={"message_id": "msg-find", "hash": "hf"},
        )
        found = repo.find_memory_by_message_id("msg-find")
        assert found is not None
        assert found["content"] == "needle"
        assert repo.find_memory_by_message_id("missing") is None


class TestGmailSyncUpsert:
    def test_resync_same_payload_is_duplicate_not_second_row(self, repo):
        record = _gmail_record("gmail-msg-99")
        engine = MagicMock()
        engine.repository = repo
        engine.get_embeddings = MagicMock(return_value=[[0.1, 0.2]])

        state = MagicMock()
        state.get_value.return_value = {}
        with patch(
            "src.core.intelligence.ingestion.sync_manager.StateRepository",
            return_value=state,
        ):
            mgr = IngestionSyncManager(engine, ingestors=[_FakeGmailIngestor([record])])
            r1 = mgr.run_sync_cycle()
            r2 = mgr.run_sync_cycle()

        assert r1["sources"]["gmail"]["added"] == 1
        assert r2["sources"]["gmail"]["duplicates"] == 1
        assert r2["sources"]["gmail"]["added"] == 0
        assert len(repo.list_memories(category="personal_index")) == 1

    def test_resync_changed_snippet_updates_single_row(self, repo):
        engine = MagicMock()
        engine.repository = repo
        engine.get_embeddings = MagicMock(return_value=[[0.1, 0.2]])

        state = MagicMock()
        state.get_value.return_value = {}
        with patch(
            "src.core.intelligence.ingestion.sync_manager.StateRepository",
            return_value=state,
        ):
            first = _gmail_record("gmail-msg-42", snippet="old snippet")
            mgr = IngestionSyncManager(engine, ingestors=[_FakeGmailIngestor([first])])
            mgr.run_sync_cycle()

            second = _gmail_record("gmail-msg-42", snippet="new snippet about venue")
            mgr = IngestionSyncManager(engine, ingestors=[_FakeGmailIngestor([second])])
            report = mgr.run_sync_cycle()

        assert report["sources"]["gmail"]["added"] == 1
        rows = repo.list_memories(category="personal_index")
        assert len(rows) == 1
        assert "new snippet about venue" in rows[0]["content"]
        assert rows[0]["metadata"]["message_id"] == "gmail-msg-42"
