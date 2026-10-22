"""Web UI production build helpers — dist integrity, version stamping, WebView cache."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_UI_BUILD_VERSION_FILE = "ui_build_version.json"
# Separate marker for what the WebView has actually LOADED. Kept distinct from the
# build stamp on purpose: the build stamp is written by the Brain when it rebuilds
# dist/ (drives `needs_web_rebuild`), so if cache-staleness also keyed off it, a
# one-click update would stamp it fresh during the rebuild and the GUI relaunch
# would then think its WebView2 cache already matched the new bundle — never
# clearing it, so the old UI kept rendering. This marker is written only by the
# GUI after it (re)loads a bundle, so "built" and "loaded" can disagree.
_UI_LOADED_VERSION_FILE = "ui_loaded_version.json"
_MAIN_BUNDLE_RE = re.compile(r'src="\./assets/(index-[^"]+\.js)"')


def repo_root() -> Path:
    home = os.environ.get("CHAD_HOME")
    if home:
        candidate = Path(home)
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[2]


def web_dist_dir(root: Path | None = None) -> Path:
    root = root or repo_root()
    return root / "src" / "clients" / "web" / "dist"


def webview_cache_dir(root: Path | None = None) -> Path:
    root = root or repo_root()
    return root / "data" / "webview_cache"


def get_git_head(root: Path | None = None) -> str | None:
    root = root or repo_root()

    # A packaged Chad.exe's CHAD_HOME is an installed payload, not a git
    # checkout, and shelling out from inside a frozen PyInstaller bundle risks
    # the child process picking up bundled DLLs over its own
    # (STATUS_DLL_INIT_FAILED / 0xC0000142 on git.exe, observed in testing via
    # invalidate_stale_webview_cache -> record_ui_loaded_version -> here).
    from src.shared.frozen_bootstrap import is_frozen

    if is_frozen():
        return None

    git = resolve_executable("git")
    if not git:
        return None
    try:
        proc = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            sha = (proc.stdout or "").strip()
            return sha or None
    except Exception as e:
        logger.debug("Could not resolve git HEAD for UI build: %s", e)
    return None


def _version_file_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "data" / _UI_BUILD_VERSION_FILE


def _load_version_record(root: Path | None = None) -> dict[str, Any]:
    path = _version_file_path(root)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def get_recorded_ui_build_sha(root: Path | None = None) -> str | None:
    sha = _load_version_record(root).get("sha")
    return sha if isinstance(sha, str) and sha else None


def get_dist_main_bundle(root: Path | None = None) -> str | None:
    index = web_dist_dir(root) / "index.html"
    if not index.is_file():
        return None
    try:
        html = index.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _MAIN_BUNDLE_RE.search(html)
    return match.group(1) if match else None


def get_recorded_main_bundle(root: Path | None = None) -> str | None:
    bundle = _load_version_record(root).get("main_bundle")
    return bundle if isinstance(bundle, str) and bundle else None


def record_ui_build_version(sha: str | None = None, root: Path | None = None) -> None:
    """Stamp the deployed web bundle so the GUI can detect stale WebView caches."""
    root = root or repo_root()
    head = sha or get_git_head(root)
    main_bundle = get_dist_main_bundle(root)
    if not head and not main_bundle:
        return
    payload: dict[str, str] = {}
    if head:
        payload["sha"] = head
    if main_bundle:
        payload["main_bundle"] = main_bundle
    path = _version_file_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _loaded_file_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "data" / _UI_LOADED_VERSION_FILE


def get_recorded_loaded_bundle(root: Path | None = None) -> str | None:
    """The main bundle the WebView last loaded (see _UI_LOADED_VERSION_FILE)."""
    path = _loaded_file_path(root)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            bundle = data.get("main_bundle") if isinstance(data, dict) else None
            return bundle if isinstance(bundle, str) and bundle else None
    except Exception:
        pass
    return None


def record_ui_loaded_version(root: Path | None = None) -> None:
    """Stamp the bundle the WebView is about to (re)load, so the next launch can tell
    whether dist/ changed underneath a now-stale WebView2 cache."""
    root = root or repo_root()
    main_bundle = get_dist_main_bundle(root)
    if not main_bundle:
        return
    payload = {"main_bundle": main_bundle}
    head = get_git_head(root)
    if head:
        payload["sha"] = head
    path = _loaded_file_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def dist_is_valid(root: Path | None = None) -> bool:
    """True when dist/index.html exists and its main bundle is on disk."""
    dist = web_dist_dir(root)
    main_bundle = get_dist_main_bundle(root)
    if not main_bundle:
        return False
    return (dist / "assets" / main_bundle).is_file()


def needs_web_rebuild(changed_files: list[str], head_sha: str | None, root: Path | None = None) -> bool:
    """Whether npm run build should run for the current deploy."""
    if any(f.strip().startswith("src/clients/web/") for f in changed_files):
        return True
    if not dist_is_valid(root):
        return True
    recorded_sha = get_recorded_ui_build_sha(root)
    if head_sha and recorded_sha != head_sha:
        return True
    recorded_bundle = get_recorded_main_bundle(root)
    current_bundle = get_dist_main_bundle(root)
    if current_bundle and recorded_bundle and current_bundle != recorded_bundle:
        return True
    return False


def resolve_executable(name: str) -> str | None:
    """Resolve a CLI for subprocess (on Windows npm must be npm.cmd, not bare 'npm')."""
    return shutil.which(name)


def _run_npm_install(web_dir: Path, timeout: int = 600) -> tuple[int, str, str]:
    npm = resolve_executable("npm")
    if not npm:
        return 127, "", "npm not found on PATH — install Node.js or add it to PATH"
    lock = web_dir / "package-lock.json"
    # Prefer npm ci on prod pulls; fall back to install when the lock is stale locally.
    for args in ([npm, "ci"] if lock.is_file() else [npm, "install"], [npm, "install"]):
        proc = subprocess.run(
            args,
            cwd=str(web_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _run_npm_build(web_dir: Path, timeout: int = 600) -> tuple[int, str, str]:
    npm = resolve_executable("npm")
    if not npm:
        return 127, "", "npm not found on PATH — install Node.js or add it to PATH"
    proc = subprocess.run(
        [npm, "run", "build"],
        cwd=str(web_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _backup_dist(root: Path) -> Path | None:
    """Snapshot the current VALID dist/ to a sibling backup so a failed rebuild can be
    rolled back. Returns the backup path, or None if there's nothing valid to back up.

    `vite build` empties dist/ before it produces output, so a mid-build failure (missing
    deps, tsc error, OOM) would otherwise leave prod serving a broken/empty bundle — a
    skeleton-only UI. The backup lets `rebuild_web_client` restore the last-good UI instead.
    """
    if not dist_is_valid(root):
        return None
    dist = web_dist_dir(root)
    backup = dist.parent / "dist.bak"
    try:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        shutil.copytree(dist, backup)
        return backup
    except Exception as e:
        logger.warning("[ UI ] Could not back up dist before rebuild: %s", e)
        return None


def _restore_dist(root: Path, backup: Path) -> bool:
    """Restore a dist backup created by `_backup_dist` over a broken/empty dist/."""
    dist = web_dist_dir(root)
    try:
        if dist.exists():
            shutil.rmtree(dist, ignore_errors=True)
        shutil.move(str(backup), str(dist))
        logger.warning("[ UI ] Restored previous web bundle after failed rebuild.")
        return True
    except Exception as e:
        logger.error("[ UI ] FAILED to restore dist backup after failed rebuild: %s", e)
        return False


def _summarize_build_failure(stdout: str, stderr: str, *, max_errors: int = 3) -> str:
    """Pull human-readable TypeScript errors out of npm/tsc output."""
    combined = f"{stderr or ''}\n{stdout or ''}"
    errors: list[str] = []
    for line in combined.splitlines():
        stripped = line.strip()
        if ": error TS" in stripped or stripped.startswith("error TS"):
            errors.append(stripped)
    if errors:
        head = errors[:max_errors]
        msg = " | ".join(head)
        if len(errors) > max_errors:
            msg += f" (+{len(errors) - max_errors} more)"
        return msg
    tail = (stderr or stdout or "").strip()
    return tail[-300:] if tail else "unknown build error"


async def rebuild_web_client(root: Path | None = None, *, install_deps: bool = False) -> tuple[bool, str]:
    """Atomically rebuild the web client in src/clients/web. Returns (ok, detail).

    Guarantees prod is NEVER left with a broken dist/: the current valid bundle is
    snapshotted first, and on any failure (install error, build error, or a build that
    exits 0 but produces an invalid dist) the snapshot is restored. The failure detail is
    always returned so callers can surface it to the user.
    """
    import asyncio

    root = root or repo_root()
    web_dir = root / "src" / "clients" / "web"
    backup = await asyncio.to_thread(_backup_dist, root)

    def _fail(detail: str) -> tuple[bool, str]:
        restored = False
        if backup is not None:
            restored = _restore_dist(root, backup)
        suffix = " (restored previous UI)" if restored else (
            " (NO previous UI to restore — dist may be broken)" if backup is None else ""
        )
        return False, f"{detail}{suffix}"

    steps: list[str] = []
    if install_deps:
        rc, out, err = await asyncio.to_thread(_run_npm_install, web_dir)
        if rc != 0:
            return _fail(f"npm install FAILED: {(err or out)[-300:]}")
        steps.append("Installed web deps")

    rc, out, err = await asyncio.to_thread(_run_npm_build, web_dir)
    if rc != 0:
        return _fail(f"web build FAILED: {_summarize_build_failure(out, err)}")

    # A zero exit code is not enough — verify the build actually produced a loadable
    # bundle. A truncated/partial build (exit 0 but missing index.html or main bundle)
    # would otherwise pass as success and brick the UI.
    if not dist_is_valid(root):
        return _fail("web build produced an INVALID dist (missing index.html or main bundle)")

    # Success: drop the backup and stamp the new build.
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
    record_ui_build_version(root=root)
    detail = "Rebuilt web client"
    if steps:
        detail = f"{steps[0]}; rebuilt web client"
    return True, detail


async def install_web_client_deps(root: Path | None = None) -> tuple[bool, str]:
    """Install Vite/npm deps without building dist/ (dev stack uses npm run dev)."""
    import asyncio

    root = root or repo_root()
    web_dir = root / "src" / "clients" / "web"
    rc, out, err = await asyncio.to_thread(_run_npm_install, web_dir)
    if rc != 0:
        return False, f"npm install FAILED: {(err or out)[-300:]}"
    return True, "Installed web deps"


def webview_cache_is_stale(root: Path | None = None) -> bool:
    """True when WebView2 may still be serving an old dist/ bundle.

    Compares the bundle on disk NOW against the one the WebView last LOADED — not
    against the build stamp. The build stamp is written when dist/ is rebuilt (by the
    Brain during a one-click update), so keying staleness off it made the GUI relaunch
    believe its cache already matched the freshly built bundle and skip the clear.
    The loaded marker only advances when the GUI loads, so a post-update relaunch
    correctly sees current_bundle != loaded_bundle and clears the cache.
    """
    root = root or repo_root()
    if not dist_is_valid(root):
        return False

    current_bundle = get_dist_main_bundle(root)
    loaded_bundle = get_recorded_loaded_bundle(root)

    if current_bundle and current_bundle != loaded_bundle:
        # Differs from what we last loaded — or was never loaded (no marker yet),
        # common on first prod run / after a manual pull. Clear to be safe.
        return True
    return False


def clear_webview_cache(root: Path | None = None) -> bool:
    cache = webview_cache_dir(root)
    if not cache.exists():
        return False
    try:
        shutil.rmtree(cache)
        cache.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error("[ UI ] Failed to clear WebView cache: %s", e)
        return False


def invalidate_stale_webview_cache(root: Path | None = None) -> bool:
    """Clear WebView2 cache when the on-disk UI bundle changed. Call before webview.start()."""
    root = root or repo_root()
    if not dist_is_valid(root):
        return False

    stale = webview_cache_is_stale(root)
    if stale:
        current_bundle = get_dist_main_bundle(root)
        loaded_bundle = get_recorded_loaded_bundle(root)
        cleared = clear_webview_cache(root)
        if cleared:
            logger.warning(
                "[ UI ] Cleared WebView cache (loaded bundle %s → on-disk %s)",
                loaded_bundle or "?",
                current_bundle or "?",
            )
    # Whether or not we cleared, record what the WebView is about to load so the
    # next launch compares against it. (Must run on the non-stale path too, else the
    # marker never initialises and every launch would re-clear.)
    record_ui_loaded_version(root=root)
    return stale
