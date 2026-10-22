"""Tests for perform_self_update stale-runtime restart scheduling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.remote import endpoints


def _fake_endpoints_file(tmp_path: Path) -> str:
    fake_file = tmp_path / "src" / "remote" / "endpoints.py"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.touch()
    return str(fake_file)


def _fake_git_current(sha: str, remote_branch: str = "master"):
    def fake_git(cmd, cwd, timeout=120):
        joined = " ".join(cmd)
        if "status" in joined and "porcelain" in joined:
            return 0, "", ""
        if "fetch" in cmd:
            return 0, "", ""
        if cmd[-1] == "HEAD":
            return 0, sha, ""
        if f"origin/{remote_branch}" in joined:
            return 0, sha, ""
        if "reset" in cmd or "checkout" in cmd:
            return 0, "", ""
        if "diff" in cmd:
            return 0, "", ""
        return 0, "", ""

    return fake_git


@pytest.mark.asyncio
async def test_update_restarts_when_brain_stale_even_if_git_current(tmp_path: Path) -> None:
    sha = "a" * 40

    with (
        patch.object(endpoints, "_run_git", side_effect=_fake_git_current(sha)),
        patch("src.shared.runtime_identity.brain_runtime_stale", return_value=(True, "sha_mismatch")),
        patch("src.shared.runtime_identity.gui_runtime_stale", return_value=(False, "ok")),
        patch("src.shared.runtime_identity.get_version_snapshot", return_value={"needs_restart": True}),
        patch("src.shared.ui_build.needs_web_rebuild", return_value=False),
        patch.object(endpoints, "__file__", _fake_endpoints_file(tmp_path)),
    ):
        result = await endpoints.perform_self_update(server=MagicMock(), force=False)

    assert result["success"] is True
    assert result["restarting"] is True
    assert result["gui_restart_recommended"] is True
    assert any("stale" in s.lower() for s in result.get("steps", []))


@pytest.mark.asyncio
async def test_force_update_always_schedules_restart(tmp_path: Path) -> None:
    sha = "b" * 40

    with (
        patch.object(endpoints, "_run_git", side_effect=_fake_git_current(sha)),
        patch("src.shared.runtime_identity.brain_runtime_stale", return_value=(False, "ok")),
        patch("src.shared.runtime_identity.gui_runtime_stale", return_value=(False, "ok")),
        patch("src.shared.runtime_identity.get_version_snapshot", return_value={"needs_restart": False}),
        patch("src.shared.ui_build.needs_web_rebuild", return_value=False),
        patch.object(endpoints, "__file__", _fake_endpoints_file(tmp_path)),
    ):
        result = await endpoints.perform_self_update(server=MagicMock(), force=True)

    assert result["success"] is True
    assert result["restarting"] is True
    assert result["gui_restart_recommended"] is True
    assert "user_requested" in result.get("restart_reasons", [])


@pytest.mark.asyncio
async def test_no_restart_when_fully_current_and_not_forced(tmp_path: Path) -> None:
    sha = "c" * 40

    with (
        patch.object(endpoints, "_run_git", side_effect=_fake_git_current(sha)),
        patch("src.shared.runtime_identity.brain_runtime_stale", return_value=(False, "ok")),
        patch("src.shared.runtime_identity.gui_runtime_stale", return_value=(False, "ok")),
        patch("src.shared.runtime_identity.get_version_snapshot", return_value={"needs_restart": False}),
        patch("src.shared.ui_build.needs_web_rebuild", return_value=False),
        patch.object(endpoints, "__file__", _fake_endpoints_file(tmp_path)),
    ):
        result = await endpoints.perform_self_update(server=None, force=False)

    assert result["success"] is True
    assert result["updated"] is False
    assert result["restarting"] is False
    assert "latest code" in result["message"].lower()


@pytest.mark.asyncio
async def test_dirty_tree_blocks_update(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_git(cmd, cwd, timeout=120):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if "porcelain" in joined:
            return 0, " M dirty.py", ""
        return 0, "", ""

    with (
        patch.object(endpoints, "_run_git", side_effect=fake_git),
        patch.object(endpoints, "__file__", _fake_endpoints_file(tmp_path)),
    ):
        result = await endpoints.perform_self_update(server=None, branch="dev", force=True)

    assert result["success"] is False
    assert "stash" in result["error"].lower()
    assert not any("fetch" in " ".join(c) for c in calls)


@pytest.mark.asyncio
async def test_non_master_branch_fetches_and_checkouts(tmp_path: Path) -> None:
    local_sha = "d" * 40
    remote_sha = "e" * 40
    calls: list[list[str]] = []

    def fake_git(cmd, cwd, timeout=120):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if "porcelain" in joined:
            return 0, "", ""
        if "fetch" in cmd and "feature-x" in cmd:
            return 0, "", ""
        if cmd[-1] == "HEAD":
            return 0, local_sha, ""
        if cmd[-1] == "origin/feature-x":
            return 0, remote_sha, ""
        if "diff" in cmd:
            return 0, "src/foo.py", ""
        if "reset" in cmd or "checkout" in cmd:
            return 0, "", ""
        return 0, "", ""

    with (
        patch.object(endpoints, "_run_git", side_effect=fake_git),
        patch("src.shared.runtime_identity.brain_runtime_stale", return_value=(False, "ok")),
        patch("src.shared.runtime_identity.gui_runtime_stale", return_value=(False, "ok")),
        patch("src.shared.runtime_identity.get_version_snapshot", return_value={"needs_restart": False}),
        patch("src.shared.ui_build.needs_web_rebuild", return_value=False),
        patch.object(endpoints, "__file__", _fake_endpoints_file(tmp_path)),
    ):
        result = await endpoints.perform_self_update(
            server=MagicMock(), branch="feature-x", force=True
        )

    assert result["success"] is True
    assert any("fetch" in " ".join(c) and "feature-x" in " ".join(c) for c in calls)
    assert any("checkout" in " ".join(c) and "origin/feature-x" in " ".join(c) for c in calls)


def test_parse_remote_branches_strips_origin_prefix() -> None:
    raw = "origin\norigin/HEAD\norigin/master\norigin/feature/foo\n"
    assert endpoints._parse_remote_branches(raw) == ["feature/foo", "master"]


def test_branch_switch_blocked_on_prod_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8767")
    assert endpoints._is_dev_brain() is False
    assert endpoints._branch_switch_blocked("dev") == (
        "Branch switching is only available on the dev stack (port 8788)."
    )
    assert endpoints._branch_switch_blocked("master") is None


def test_branch_switch_allowed_on_dev_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8788")
    assert endpoints._is_dev_brain() is True
    assert endpoints._branch_switch_blocked("feature/foo") is None


@pytest.mark.asyncio
async def test_dev_stack_skips_dist_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8788")
    sha = "f" * 40
    rebuild_called = False

    async def fake_rebuild(*args, **kwargs):
        nonlocal rebuild_called
        rebuild_called = True
        return True, "should not run"

    with (
        patch.object(endpoints, "_run_git", side_effect=_fake_git_current(sha)),
        patch("src.shared.runtime_identity.brain_runtime_stale", return_value=(False, "ok")),
        patch("src.shared.runtime_identity.gui_runtime_stale", return_value=(True, "sha_mismatch")),
        patch("src.shared.runtime_identity.get_version_snapshot", return_value={"needs_restart": False}),
        patch("src.shared.ui_build.needs_web_rebuild", return_value=True),
        patch("src.shared.ui_build.rebuild_web_client", side_effect=fake_rebuild),
        patch.object(endpoints, "__file__", _fake_endpoints_file(tmp_path)),
    ):
        result = await endpoints.perform_self_update(server=MagicMock(), force=True)

    assert result["success"] is True
    assert rebuild_called is False
    assert result["restarting"] is True


def test_validate_branch_name_rejects_invalid() -> None:
    assert endpoints._validate_branch_name("good/branch-1") is True
    assert endpoints._validate_branch_name("../evil") is False
    assert endpoints._validate_branch_name("-bad") is False


def test_meaningful_dirty_paths_ignores_runtime_stamps() -> None:
    porcelain = "\n".join([
        " M data/ui_build_version.json",
        " M data/dev_logs/dev_stack.pids",
        " M src/foo.py",
    ])
    assert endpoints._meaningful_dirty_paths(porcelain) == ["src/foo.py"]
    assert endpoints._meaningful_dirty_paths("") == []
