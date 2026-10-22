"""Chrome Activity rows stay local-only (issue #619).

Browser history is high-volume: even when ``memory.storage: local_then_supabase``
mirrors other memories, ``source=chrome`` must write sqlite only and never call
the Supabase mirror. Fixture sync — no live Chrome DB required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.ingestion.base import BaseIngestor
from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager
from src.infrastructure.repositories.memory_backends import LocalMemoryBackend
from src.infrastructure.repositories.memory_repository import MemoryRepository


@pytest.fixture
def repo_mirror_mode(tmp_path, monkeypatch):
    """Supabase credentials present + local_then_supabase — chrome must still skip mirror."""
    from src.infrastructure.database import local_store
    from src.infrastructure.repositories import memory_repository as mr_mod

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "chrome_local.db"))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)

    fake_sb = MagicMock()
    fake_sb.enabled = True
    monkeypatch.setattr(mr_mod, "get_supabase_manager", lambda: fake_sb)
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        lambda key, default=None: (
            "local_then_supabase" if key == "memory.storage" else default
        ),
    )

    repo = MemoryRepository()
    assert isinstance(repo._backend, LocalMemoryBackend)
    assert repo._memory_storage_mode() == "local_then_supabase"
    # Spy on cloud create so we can assert chrome never mirrors.
    repo._cloud = MagicMock()
    repo._cloud.create_memory = MagicMock(return_value=99)
    repo._cloud.has_memory_with_hash = MagicMock(return_value=False)
    repo._cloud.expire_memories_by_source = MagicMock(return_value=True)
    return repo


class _FakeChromeIngestor(BaseIngestor):
    def __init__(self, records: list[dict]):
        self._records = records

    def get_source_name(self) -> str:
        return "chrome"

    def ingest(self, last_sync=None):
        return list(self._records)


def _chrome_record(
    *,
    url: str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    title: str = "Test Video",
    ts: str = "2026-07-20T15:00:00+00:00",
) -> dict:
    content = f"Watched YouTube: '{title}'"
    ingestor = _FakeChromeIngestor([])
    item_hash = ingestor.compute_hash(f"chrome_{url}_123")
    return {
        "content": content,
        "importance": 0.6,
        "metadata": {
            "source": "chrome",
            "type": "youtube_video",
            "url": url,
            "title": title,
            "domain": "www.youtube.com",
            "timestamp": ts,
            "visit_count": 1,
            "hash": item_hash,
        },
    }


class TestChromeNeverMirrors:
    def test_create_chrome_skips_supabase_mirror(self, repo_mirror_mode):
        repo = repo_mirror_mode
        mid = repo.create_memory(
            content="Watched YouTube: 'local only'",
            category="personal_index",
            importance=0.6,
            source="chrome",
            embedding=[0.1, 0.2],
            metadata={"source": "chrome", "hash": "abc"},
        )
        assert mid > 0
        rows = repo._local.list_memories("personal_index", None, 10, 0)
        assert len(rows) == 1
        assert rows[0]["source"] == "chrome"
        repo._cloud.create_memory.assert_not_called()

    def test_create_non_chrome_also_skips_memory_mirror(self, repo_mirror_mode):
        """Memories table is never mirrored — only graph + prompts backup."""
        repo = repo_mirror_mode
        mid = repo.create_memory(
            content="user note",
            category="general",
            source="user",
            embedding=[0.1],
            metadata={"hash": "note1"},
        )
        assert mid > 0
        repo._cloud.create_memory.assert_not_called()

    def test_filesystem_and_gmail_stay_local(self, repo_mirror_mode):
        repo = repo_mirror_mode
        repo.create_memory(
            content="File: 'budget.xlsx' at C:/docs/budget.xlsx",
            category="personal_index",
            source="filesystem",
            metadata={"source": "filesystem", "hash": "fs1"},
        )
        repo.upsert_memory_by_message_id(
            message_id="m1",
            content="Email from a: 'Hi'\nSnippet: hello",
            category="personal_index",
            source="gmail",
            metadata={"message_id": "m1", "hash": "g1", "source": "gmail"},
        )
        repo.upsert_memory_by_file_id(
            file_id="drive1",
            content="Doc: notes",
            category="drive_catalog",
            source="drive_catalog",
            metadata={"file_id": "drive1", "hash": "d1"},
        )
        assert len(repo._local.list_memories("personal_index", None, 20, 0)) == 2
        assert len(repo._local.list_memories("drive_catalog", None, 20, 0)) == 1
        repo._cloud.create_memory.assert_not_called()

    def test_chrome_cloud_primary_escape_hatch_still_writes_local(
        self, tmp_path, monkeypatch
    ):
        """Even memory.storage=supabase must not put chrome visits in cloud."""
        from src.infrastructure.database import local_store
        from src.infrastructure.repositories import memory_repository as mr_mod
        from src.infrastructure.repositories.memory_backends import SupabaseMemoryBackend

        monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "chrome_escape.db"))
        monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)

        fake_sb = MagicMock()
        fake_sb.enabled = True
        monkeypatch.setattr(mr_mod, "get_supabase_manager", lambda: fake_sb)
        monkeypatch.setattr(
            "src.shared.config_manager.ConfigManager.get",
            lambda key, default=None: ("supabase" if key == "memory.storage" else default),
        )

        repo = MemoryRepository()
        assert isinstance(repo._backend, SupabaseMemoryBackend)
        repo._cloud = MagicMock()
        repo._cloud.create_memory = MagicMock(return_value=55)

        mid = repo.create_memory(
            content="Visited Web Page: 'private'",
            category="personal_index",
            source="chrome",
            metadata={"source": "chrome", "hash": "h1"},
        )
        assert mid > 0
        assert len(repo._local.list_memories("personal_index", None, 10, 0)) == 1
        repo._cloud.create_memory.assert_not_called()


class TestChromeSyncLocalFirst:
    def test_sync_persists_chrome_fixture_to_local_sqlite(self, repo_mirror_mode):
        repo = repo_mirror_mode
        engine = MagicMock()
        engine.repository = repo
        engine.get_embeddings = MagicMock(return_value=[[0.1, 0.2]])

        state = MagicMock()
        state.get_value.return_value = {}
        record = _chrome_record()

        with patch(
            "src.core.intelligence.ingestion.sync_manager.StateRepository",
            return_value=state,
        ):
            mgr = IngestionSyncManager(
                engine, ingestors=[_FakeChromeIngestor([record])]
            )
            report = mgr.run_sync_cycle()

        assert report["sources"]["chrome"]["added"] == 1
        rows = repo._local.list_memories("personal_index", None, 10, 0)
        assert len(rows) == 1
        assert rows[0]["metadata"]["source"] == "chrome"
        assert "YouTube" in rows[0]["content"]
        repo._cloud.create_memory.assert_not_called()
