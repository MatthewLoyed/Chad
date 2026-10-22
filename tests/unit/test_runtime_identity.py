"""Tests for deployed vs running runtime identity (update staleness detection)."""

from __future__ import annotations

import json
from pathlib import Path

from src.shared import runtime_identity as ri


def _write_runtime(root: Path, role: str, sha_full: str, sha_short: str | None = None) -> None:
    path = root / "data" / "runtime" / f"{role}_runtime.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "role": role,
                "sha_full": sha_full,
                "sha_short": sha_short or sha_full[:8],
                "boot_id": "test-boot",
                "pid": 99999,
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def test_brain_stale_when_disk_ahead_of_runtime_file(tmp_path: Path, monkeypatch) -> None:
    _write_runtime(tmp_path, "brain", "aaa" * 10 + "old")
    monkeypatch.setattr(ri, "get_deployed_info", lambda root=None: {"sha_full": "bbb" * 10 + "new", "sha_short": "bbbbbbb"})
    stale, reason = ri.brain_runtime_stale(tmp_path)
    assert stale is True
    assert reason == "sha_mismatch"


def test_brain_not_stale_when_shas_match(tmp_path: Path, monkeypatch) -> None:
    sha = "abc123def456"
    _write_runtime(tmp_path, "brain", sha)
    monkeypatch.setattr(ri, "get_deployed_info", lambda root=None: {"sha_full": sha, "sha_short": "abc123de"})
    stale, reason = ri.brain_runtime_stale(tmp_path)
    assert stale is False
    assert reason == "ok"


def test_version_snapshot_marks_stale_brain(tmp_path: Path, monkeypatch) -> None:
    _write_runtime(tmp_path, "brain", "running_sha_full_xxxxxxxx")
    _write_runtime(tmp_path, "gui", "running_sha_full_xxxxxxxx")
    monkeypatch.setattr(
        ri,
        "get_deployed_info",
        lambda root=None: {
            "sha_full": "deployed_sha_full_yyyyyyyy",
            "sha_short": "deployed",
            "branch": "master",
            "date": "2026-06-25T10:00:00-05:00",
            "main_bundle": "index-aaaa.js",
            "recorded_bundle": "index-aaaa.js",
        },
    )
    monkeypatch.setattr(ri, "gui_runtime_stale", lambda root=None: (False, "ok"))
    snap = ri.get_version_snapshot(tmp_path)
    assert snap["stale"]["brain"] is True
    assert snap["needs_restart"] is True
    assert snap["running"]["sha_full"] == "running_sha_full_xxxxxxxx"
    assert snap["deployed"]["sha"] == "deployed"


def test_record_runtime_identity_writes_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ri, "_git_info", lambda root: {"sha_full": "f" * 40, "sha_short": "ffffffff", "branch": "master"})
    monkeypatch.chdir(tmp_path)
    ident = ri.record_runtime_identity("brain", root=tmp_path)
    assert ident["sha_full"] == "f" * 40
    path = tmp_path / "data" / "runtime" / "brain_runtime.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["boot_id"] == ident["boot_id"]
