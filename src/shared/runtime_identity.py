"""Process runtime identity — what code is *actually loaded*, not just on disk.

Professional self-updating desktop apps distinguish:
  - **deployed** — git HEAD / build artifacts on disk (what a restart would load)
  - **running** — SHA captured once at process import (what this PID is executing)

The Brain and GUI each stamp ``data/runtime/{role}_runtime.json`` at boot. Update
logic compares deployed vs running and restarts when they diverge — so a ``git pull``
or IDE merge without a process recycle cannot leave stale bytecode in memory while
the Settings screen falsely reports "up to date".
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

RuntimeRole = Literal["brain", "gui"]

_RUNTIME_DIR = "runtime"
# Keyed by (role, str(root)) — a bare `role` key would let one root's cached
# identity leak into a lookup for a different root within the same process
# (e.g. tests using tmp_path roots after src.remote.server's real import-time
# `record_runtime_identity("brain")` already populated the cache for this PID).
_IN_PROCESS: dict[tuple[RuntimeRole, str], dict[str, Any]] = {}


def repo_root() -> Path:
    from src.shared.ui_build import repo_root as _root

    return _root()


def _runtime_path(role: RuntimeRole, root: Path | None = None) -> Path:
    return (root or repo_root()) / "data" / _RUNTIME_DIR / f"{role}_runtime.json"


def _git_info(root: Path) -> dict[str, Any]:
    import subprocess
    import shutil

    info: dict[str, Any] = {
        "sha_full": None,
        "sha_short": None,
        "branch": None,
        "date": None,
    }

    # A packaged Chad.exe's CHAD_HOME is an installed payload, not a git
    # checkout, and there's nothing meaningful to compare it against — worse,
    # shelling out from inside a frozen PyInstaller bundle risks the child
    # process picking up bundled DLLs over its own (STATUS_DLL_INIT_FAILED /
    # 0xC0000142 on git.exe, observed in testing). Skip entirely when frozen.
    from src.shared.frozen_bootstrap import is_frozen

    if is_frozen():
        return info

    git = shutil.which("git")
    if not git:
        return info
    # The prod GUI launches windowless (pythonw / frozen Chad.exe), so it has no
    # console. Each git.exe child would otherwise allocate its own console window
    # for an instant — the four git calls below are exactly the "four terminals
    # flashing" a user sees at startup. CREATE_NO_WINDOW suppresses them (0/no-op
    # off Windows).
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for key, args in (
            ("sha_full", ["rev-parse", "HEAD"]),
            ("sha_short", ["rev-parse", "--short", "HEAD"]),
            ("branch", ["rev-parse", "--abbrev-ref", "HEAD"]),
            ("date", ["show", "-s", "--format=%cI", "HEAD"]),
        ):
            proc = subprocess.run(
                [git, *args],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=no_window,
            )
            if proc.returncode == 0:
                val = (proc.stdout or "").strip()
                if val:
                    info[key] = val
        branch = info.get("branch")
        if branch == "chad-dev":
            proc = subprocess.run(
                [git, "rev-parse", "--abbrev-ref", "@{upstream}"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=no_window,
            )
            if proc.returncode == 0:
                upstream = (proc.stdout or "").strip()
                if upstream.startswith("origin/"):
                    info["branch"] = upstream[len("origin/") :]
    except Exception as exc:
        logger.debug("[ RUNTIME ] git info failed: %s", exc)
    return info


def get_deployed_info(root: Path | None = None) -> dict[str, Any]:
    """Git HEAD on disk — what the next Brain/GUI boot would load."""
    root = root or repo_root()
    git = _git_info(root)
    from src.shared.ui_build import get_dist_main_bundle, get_recorded_main_bundle

    return {
        **git,
        "main_bundle": get_dist_main_bundle(root),
        "recorded_bundle": get_recorded_main_bundle(root),
    }


def record_runtime_identity(role: RuntimeRole, *, root: Path | None = None) -> dict[str, Any]:
    """Capture and persist this process's identity. Call once at early boot."""
    root = root or repo_root()
    git = _git_info(root)
    identity: dict[str, Any] = {
        "role": role,
        "sha_full": git.get("sha_full"),
        "sha_short": git.get("sha_short"),
        "branch": git.get("branch"),
        "boot_id": str(uuid.uuid4()),
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if role == "gui":
        from src.shared.ui_build import get_dist_main_bundle

        identity["main_bundle"] = get_dist_main_bundle(root)

    _IN_PROCESS[(role, str(root))] = identity
    path = _runtime_path(role, root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("[ RUNTIME ] Could not write %s: %s", path, exc)
    return identity


def get_in_process_identity(role: RuntimeRole, root: Path | None = None) -> Optional[dict[str, Any]]:
    """Identity for the current process (Brain/GUI), if recorded this boot for this root."""
    root = root or repo_root()
    rec = _IN_PROCESS.get((role, str(root)))
    if rec and rec.get("pid") == os.getpid():
        return dict(rec)
    return None


def read_runtime_record(role: RuntimeRole, root: Path | None = None) -> Optional[dict[str, Any]]:
    """Read the on-disk runtime stamp for ``role`` (may be another PID if stale file)."""
    in_proc = get_in_process_identity(role, root)
    if in_proc:
        return in_proc

    path = _runtime_path(role, root)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def brain_runtime_stale(root: Path | None = None) -> tuple[bool, str]:
    """True when the live Brain process loaded a different SHA than disk HEAD."""
    root = root or repo_root()
    deployed = get_deployed_info(root)
    running = get_in_process_identity("brain", root) or read_runtime_record("brain", root)
    dep_sha = deployed.get("sha_full")
    run_sha = (running or {}).get("sha_full")
    if not dep_sha or not run_sha:
        return False, "unknown"
    if dep_sha != run_sha:
        return True, "sha_mismatch"
    return False, "ok"


def gui_runtime_stale(root: Path | None = None) -> tuple[bool, str]:
    """True when the GUI process or WebView bundle lags deployed artifacts."""
    root = root or repo_root()
    deployed = get_deployed_info(root)
    running = read_runtime_record("gui", root)
    dep_sha = deployed.get("sha_full")
    run_sha = (running or {}).get("sha_full")
    if dep_sha and run_sha and dep_sha != run_sha:
        return True, "sha_mismatch"
    try:
        from src.shared.ui_build import webview_cache_is_stale

        if webview_cache_is_stale(root):
            return True, "webview_cache"
    except Exception:
        pass
    dep_bundle = deployed.get("main_bundle")
    run_bundle = (running or {}).get("main_bundle")
    if dep_bundle and run_bundle and dep_bundle != run_bundle:
        return True, "bundle_mismatch"
    return False, "ok"


def get_version_snapshot(root: Path | None = None) -> dict[str, Any]:
    """Full deployed vs running snapshot for Settings and /api/version."""
    root = root or repo_root()
    deployed = get_deployed_info(root)
    brain_running = get_in_process_identity("brain", root) or read_runtime_record("brain", root)
    gui_running = read_runtime_record("gui", root)
    brain_stale, brain_reason = brain_runtime_stale(root)
    gui_stale, gui_reason = gui_runtime_stale(root)

    running_sha = (brain_running or {}).get("sha_short") or "unknown"
    deployed_sha = deployed.get("sha_short") or "unknown"

    return {
        "deployed": {
            "sha": deployed_sha,
            "sha_full": deployed.get("sha_full"),
            "branch": deployed.get("branch"),
            "date": deployed.get("date"),
            "main_bundle": deployed.get("main_bundle"),
        },
        "running": {
            "sha": running_sha,
            "sha_full": (brain_running or {}).get("sha_full"),
            "boot_id": (brain_running or {}).get("boot_id"),
            "pid": (brain_running or {}).get("pid"),
            "started_at": (brain_running or {}).get("started_at"),
        },
        "gui": {
            "sha": (gui_running or {}).get("sha_short"),
            "sha_full": (gui_running or {}).get("sha_full"),
            "pid": (gui_running or {}).get("pid"),
            "started_at": (gui_running or {}).get("started_at"),
            "main_bundle": (gui_running or {}).get("main_bundle"),
        } if gui_running else None,
        "stale": {
            "brain": brain_stale,
            "brain_reason": brain_reason if brain_stale else None,
            "gui": gui_stale,
            "gui_reason": gui_reason if gui_stale else None,
        },
        "needs_restart": brain_stale or gui_stale,
        # Legacy flat fields — ``sha`` is what the Brain is *running*, not disk HEAD.
        "sha": running_sha,
        "branch": deployed.get("branch"),
        "date": deployed.get("date"),
        "deployed_sha": deployed_sha,
    }
