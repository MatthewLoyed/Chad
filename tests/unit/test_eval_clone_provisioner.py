"""Unit tests for the #596 clone-provisioning pipeline (src/tools/dev/eval_clone_provisioner.py).

Uses a REAL local git repo (tmp_path) instead of mocking subprocess — `git worktree add` against
a scratch repo is fast and exercises the actual command, which is what matters here (SHA
resolution, idempotent reuse, worktree removal)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.tools.dev import eval_clone_provisioner as provisioner
from src.tools.dev import openhands_dispatch


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "config").mkdir()
    (repo / "config" / "persona").mkdir()
    (repo / "config" / "persona" / "identity.md").write_text("overlay identity", encoding="utf-8")
    (repo / "README.md").write_text("v1", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "initial"], repo)
    # secrets.yaml is gitignored in the real repo (never a tracked file), so a fresh `git
    # worktree add` checkout never contains it — create it untracked here to match that.
    (repo / "config" / "secrets.yaml").write_text("api_key: real-secret", encoding="utf-8")
    return repo


def _head_sha(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                          capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_provision_pins_sha_and_copies_persona(tmp_path, source_repo):
    sha = _head_sha(source_repo)
    clones_root = tmp_path / "clones"

    result = provisioner.provision_eval_clone(
        sha, job_id="job1", clones_root=clones_root, source_repo=source_repo,
    )

    assert result["success"] is True
    dest = Path(result["path"])
    assert dest == clones_root / "job1"
    assert result["sha"] == sha
    assert result["persona_copied"] is True
    assert (dest / "config" / "persona" / "identity.md").read_text(encoding="utf-8") == "overlay identity"


def test_provision_never_copies_secrets(tmp_path, source_repo):
    sha = _head_sha(source_repo)
    result = provisioner.provision_eval_clone(
        sha, job_id="job2", clones_root=tmp_path / "clones", source_repo=source_repo,
    )
    dest = Path(result["path"])
    assert not (dest / "config" / "secrets.yaml").exists()
    # secrets are handed back as an env var pointing at the HOST file, not copied in
    assert result["secrets_env"]["CHAD_SECRETS_PATH"] == str(source_repo / "config" / "secrets.yaml")


def test_provision_grants_dispatch_allowlist_via_env(tmp_path, source_repo, monkeypatch):
    sha = _head_sha(source_repo)
    result = provisioner.provision_eval_clone(
        sha, job_id="job3", clones_root=tmp_path / "clones", source_repo=source_repo,
    )
    dest = Path(result["path"])

    # Without the env grant, the workspace guard refuses (it's a git repo, epic non-goal #576).
    assert openhands_dispatch._workspace_guard(dest) is not None

    for k, v in result["allowlist_env"].items():
        monkeypatch.setenv(k, v)
    assert openhands_dispatch._workspace_guard(dest) is None


def test_provision_is_idempotent_at_same_sha(tmp_path, source_repo):
    sha = _head_sha(source_repo)
    clones_root = tmp_path / "clones"
    first = provisioner.provision_eval_clone(sha, job_id="job4", clones_root=clones_root,
                                              source_repo=source_repo)
    second = provisioner.provision_eval_clone(sha, job_id="job4", clones_root=clones_root,
                                               source_repo=source_repo)
    assert first["success"] and second["success"]
    assert first["path"] == second["path"]
    assert second["sha"] == sha


def test_provision_recreates_clone_when_sha_changes(tmp_path, source_repo):
    clones_root = tmp_path / "clones"
    sha1 = _head_sha(source_repo)
    provisioner.provision_eval_clone(sha1, job_id="job5", clones_root=clones_root,
                                      source_repo=source_repo)

    (source_repo / "README.md").write_text("v2", encoding="utf-8")
    _git(["add", "-A"], source_repo)
    _git(["commit", "-q", "-m", "second"], source_repo)
    sha2 = _head_sha(source_repo)

    result = provisioner.provision_eval_clone(sha2, job_id="job5", clones_root=clones_root,
                                               source_repo=source_repo)
    assert result["success"] is True
    dest = Path(result["path"])
    assert result["sha"] == sha2
    assert (dest / "README.md").read_text(encoding="utf-8") == "v2"


def test_provision_unknown_sha_fails_cleanly(tmp_path, source_repo):
    result = provisioner.provision_eval_clone(
        "0" * 40, job_id="job6", clones_root=tmp_path / "clones", source_repo=source_repo,
    )
    assert result["success"] is False
    assert "error" in result


def test_deprovision_removes_worktree(tmp_path, source_repo):
    sha = _head_sha(source_repo)
    result = provisioner.provision_eval_clone(
        sha, job_id="job7", clones_root=tmp_path / "clones", source_repo=source_repo,
    )
    dest = Path(result["path"])
    assert dest.exists()

    out = provisioner.deprovision_eval_clone(dest, source_repo=source_repo)
    assert out["success"] is True
    assert not dest.exists()
