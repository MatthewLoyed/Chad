import os
from datetime import datetime, timedelta, timezone

import pytest

from src.core.intelligence.ingestion.filesystem import (
    DEFAULT_EXCLUDE_GLOBS,
    FilesystemCatalogIngestor,
    format_catalog_entry,
    is_excluded,
    is_hidden_dir,
    is_model_weight,
    is_noise_file,
    is_video,
    own_storage_exclude_globs,
)


def test_is_excluded_matches_node_modules():
    assert is_excluded(
        "C:/projects/app/node_modules/pkg/index.js", ["**/node_modules/**"]
    )
    assert not is_excluded("C:/projects/app/src/index.js", ["**/node_modules/**"])


def test_is_excluded_normalizes_backslashes():
    assert is_excluded(
        r"C:\projects\app\.git\HEAD", ["**/.git/**"]
    )


def test_is_video_detects_common_extensions():
    assert is_video("C:/Videos/clip.mp4")
    assert is_video("C:/Videos/clip.MKV")
    assert not is_video("C:/Documents/notes.txt")


def test_is_hidden_dir():
    assert is_hidden_dir(".lmstudio")
    assert is_hidden_dir(".git")
    assert not is_hidden_dir("Documents")


def test_is_model_weight_detects_common_formats():
    assert is_model_weight("C:/ComfyUI/models/sdxl.safetensors")
    assert is_model_weight("C:/models/llama.gguf")
    assert not is_model_weight("C:/Documents/notes.txt")


def test_sqlite_sidecar_files_are_excluded_by_default():
    assert is_excluded("C:/data/app.db-wal", DEFAULT_EXCLUDE_GLOBS)
    assert is_excluded("C:/data/app.db-shm", DEFAULT_EXCLUDE_GLOBS)
    assert is_excluded("C:/data/app.db-journal", DEFAULT_EXCLUDE_GLOBS)
    assert not is_excluded("C:/data/app.db", DEFAULT_EXCLUDE_GLOBS)


def test_own_storage_exclude_globs_includes_main_db_and_sidecars(monkeypatch, tmp_path):
    from src.infrastructure.database import local_store

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "mem.db"))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)

    globs = own_storage_exclude_globs()
    expected_main = str(tmp_path / "mem.db").replace("\\", "/")
    assert expected_main in globs
    assert expected_main + "-wal" in globs
    assert expected_main + "-shm" in globs
    assert expected_main + "-journal" in globs


def test_is_noise_file_detects_unity_meta():
    assert is_noise_file("C:/Project/Assets/Player.cs.meta")
    assert not is_noise_file("C:/Project/Assets/Player.cs")


def test_format_catalog_entry_shape():
    entry = format_catalog_entry("notes.txt", "C:/Documents/notes.txt", "2026-05-14T00:00:00+00:00")
    assert entry == (
        "File: 'notes.txt' at C:/Documents/notes.txt\n"
        "Last Modified: 2026-05-14T00:00:00+00:00"
    )


def _configure(monkeypatch, **overrides):
    from src.shared.config_manager import ConfigManager

    defaults = {
        "filesystem_catalog.enabled": True,
        "filesystem_catalog.roots": [],
        "filesystem_catalog.exclude_globs": ["**/node_modules/**", "**/.git/**"],
        "filesystem_catalog.skip_videos": True,
        "filesystem_catalog.skip_hidden_dirs": True,
        "filesystem_catalog.skip_model_weights": True,
        "filesystem_catalog.max_file_size_mb": 1024,
        "filesystem_catalog.max_files": 20000,
    }
    defaults.update(overrides)

    def fake_get(key_path, default=None):
        return defaults.get(key_path, default)

    monkeypatch.setattr(ConfigManager, "get", staticmethod(fake_get))


def test_ingest_returns_empty_when_disabled(monkeypatch, tmp_path):
    _configure(monkeypatch, **{"filesystem_catalog.enabled": False, "filesystem_catalog.roots": [str(tmp_path)]})
    assert FilesystemCatalogIngestor().ingest() == []


def test_ingest_returns_empty_when_no_roots(monkeypatch):
    _configure(monkeypatch, **{"filesystem_catalog.roots": []})
    assert FilesystemCatalogIngestor().ingest() == []


def test_ingest_catalogs_files_under_root(monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "budget.xlsx").write_text("data")
    excluded_dir = tmp_path / "node_modules"
    excluded_dir.mkdir()
    (excluded_dir / "pkg.js").write_text("noise")
    (tmp_path / "clip.mp4").write_bytes(b"binary")

    _configure(monkeypatch, **{"filesystem_catalog.roots": [str(tmp_path)]})

    items = FilesystemCatalogIngestor().ingest()
    names = {item["metadata"]["name"] for item in items}

    assert names == {"notes.txt", "budget.xlsx"}
    assert all(item["metadata"]["source"] == "filesystem" for item in items)
    assert all("hash" in item["metadata"] for item in items)


def test_ingest_respects_last_sync_watermark(monkeypatch, tmp_path):
    old_file = tmp_path / "old.txt"
    old_file.write_text("old")
    old_time = (datetime.now(tz=timezone.utc) - timedelta(days=10)).timestamp()
    os.utime(old_file, (old_time, old_time))

    new_file = tmp_path / "new.txt"
    new_file.write_text("new")

    _configure(monkeypatch, **{"filesystem_catalog.roots": [str(tmp_path)]})

    last_sync = datetime.now(tz=timezone.utc) - timedelta(days=1)
    items = FilesystemCatalogIngestor().ingest(last_sync=last_sync)
    names = {item["metadata"]["name"] for item in items}

    assert names == {"new.txt"}


def test_ingest_prunes_hidden_dirs(monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    hidden_dir = tmp_path / ".lmstudio"
    hidden_dir.mkdir()
    (hidden_dir / "model.gguf").write_text("weights")

    _configure(monkeypatch, **{"filesystem_catalog.roots": [str(tmp_path)]})

    items = FilesystemCatalogIngestor().ingest()
    names = {item["metadata"]["name"] for item in items}
    assert names == {"notes.txt"}


def test_ingest_skips_model_weight_extensions(monkeypatch, tmp_path):
    (tmp_path / "checkpoint.safetensors").write_text("weights")
    (tmp_path / "notes.txt").write_text("hello")

    _configure(monkeypatch, **{"filesystem_catalog.roots": [str(tmp_path)]})

    items = FilesystemCatalogIngestor().ingest()
    names = {item["metadata"]["name"] for item in items}
    assert names == {"notes.txt"}


def test_ingest_skips_files_over_size_cutoff(monkeypatch, tmp_path):
    small = tmp_path / "small.txt"
    small.write_text("x" * 10)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 2048)

    _configure(monkeypatch, **{
        "filesystem_catalog.roots": [str(tmp_path)],
        "filesystem_catalog.max_file_size_mb": 0.001,  # ~1KB cutoff
    })

    items = FilesystemCatalogIngestor().ingest()
    names = {item["metadata"]["name"] for item in items}
    assert names == {"small.txt"}


def test_ingest_respects_max_files_cap(monkeypatch, tmp_path):
    for i in range(5):
        (tmp_path / f"file_{i}.txt").write_text("x")

    _configure(monkeypatch, **{"filesystem_catalog.roots": [str(tmp_path)], "filesystem_catalog.max_files": 2})

    items = FilesystemCatalogIngestor().ingest()
    assert len(items) <= 2


def test_ingest_max_files_cap_keeps_oldest_across_dirs(monkeypatch, tmp_path):
    # Regression: truncation must pick globally-oldest files, not just the first
    # ones os.walk happens to visit -- otherwise the watermark advances past an
    # older file in a later-visited directory and it's silently lost forever.
    dir_a = tmp_path / "a_dir"
    dir_a.mkdir()
    dir_b = tmp_path / "b_dir"
    dir_b.mkdir()

    newer = dir_a / "newer.txt"
    newer.write_text("new")
    older = dir_b / "older.txt"
    older.write_text("old")

    now = datetime.now(tz=timezone.utc).timestamp()
    os.utime(newer, (now, now))
    os.utime(older, (now - 100000, now - 100000))

    _configure(monkeypatch, **{"filesystem_catalog.roots": [str(tmp_path)], "filesystem_catalog.max_files": 1})

    items = FilesystemCatalogIngestor().ingest()
    assert len(items) == 1
    assert items[0]["metadata"]["name"] == "older.txt"


def test_ingest_never_catalogs_its_own_database(monkeypatch, tmp_path):
    # Regression for a real production bug: when a configured root contains Chad's own
    # local_chad.db (e.g. the repo lives under a scanned Documents/ tree), every write
    # updates the db's own mtime, so it looked "new" on every single sync cycle --
    # infinite self-referential loop that never converges. Simulate that layout: the
    # db lives INSIDE the scanned root.
    from src.infrastructure.database import local_store

    db_path = tmp_path / "data" / "local_chad.db"
    db_path.parent.mkdir()
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db_path))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    local_store.init_local_db()  # creates the real sqlite file on disk

    (tmp_path / "notes.txt").write_text("hello")

    _configure(monkeypatch, **{"filesystem_catalog.roots": [str(tmp_path)]})

    items = FilesystemCatalogIngestor().ingest()
    names = {item["metadata"]["name"] for item in items}

    assert names == {"notes.txt"}
    assert "local_chad.db" not in names


def test_ingest_skips_missing_root(monkeypatch, tmp_path):
    missing_root = str(tmp_path / "does_not_exist")
    _configure(monkeypatch, **{"filesystem_catalog.roots": [missing_root]})
    assert FilesystemCatalogIngestor().ingest() == []
