"""Wait for the current Chad GUI to exit, then relaunch it (post-update restart).

Spawned detached by the running GUI so the WebView API can return its response
before this process closes the window. Mirrors how professional desktop apps
restart themselves after an update.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _wait_for_pid_exit(pid: int, timeout_s: float = 45.0) -> None:
    import psutil

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not psutil.pid_exists(pid):
            return
        time.sleep(0.2)
    print(f"[ RELAUNCH ] Timed out waiting for PID {pid} to exit", file=sys.stderr)


def _relaunch_env(repo: Path) -> dict[str, str]:
    """Preserve dev-vs-prod transport from the GUI that spawned this helper."""
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", ".")
    env.setdefault("CHAD_MODE", "cloud")
    server_url = env.get("CHAD_SERVER_URL", "ws://localhost:8767/ws")
    if ":8788" in server_url:
        env.setdefault("CHAD_SERVER_URL", "ws://localhost:8788/ws")
        env.setdefault("CHAD_UI_DEV", "true")
    else:
        env.setdefault("CHAD_SERVER_URL", "ws://localhost:8767/ws")
        env.setdefault("CHAD_UI_DEV", "false")
    return env


def _is_dev_stack(env: dict[str, str]) -> bool:
    return ":8788" in env.get("CHAD_SERVER_URL", "")


def _launch_dev_stack(repo: Path, env: dict[str, str]) -> None:
    """Restart Vite + dev Brain + dev GUI (dev has no supervisor loop)."""
    script = repo / "scripts" / "prod" / "launch_dev.ps1"
    if not script.is_file():
        print(f"[ RELAUNCH ] launch_dev.ps1 not found at {script}", file=sys.stderr)
        _launch_gui(repo, env)
        return

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=str(repo),
        env=env,
        creationflags=creationflags,
    )


def _launch_gui(repo: Path, env: dict[str, str]) -> None:
    os.chdir(repo)

    # Clear any stale WebView2 chunks before the fresh window loads dist/.
    try:
        sys.path.insert(0, str(repo))
        from src.shared.ui_build import invalidate_stale_webview_cache

        invalidate_stale_webview_cache(repo)
    except Exception as exc:
        print(f"[ RELAUNCH ] WebView cache check skipped: {exc}", file=sys.stderr)

    from src.shared.frozen_bootstrap import is_frozen

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    if is_frozen():
        subprocess.Popen([sys.executable], cwd=str(repo), env=env, creationflags=creationflags)
        return

    pythonw = repo / ".venv" / "Scripts" / "pythonw.exe"
    if pythonw.is_file():
        cmd = [str(pythonw), "main_gui.py"]
    else:
        cmd = [sys.executable, "main_gui.py"]

    subprocess.Popen(cmd, cwd=str(repo), env=env, creationflags=creationflags)


def main() -> int:
    parser = argparse.ArgumentParser(description="Relaunch Chad GUI after an update")
    parser.add_argument("--wait-pid", type=int, required=True, help="PID to wait on before relaunching")
    args = parser.parse_args()

    repo = _repo_root()
    _wait_for_pid_exit(args.wait_pid)
    env = _relaunch_env(repo)
    if _is_dev_stack(env):
        _launch_dev_stack(repo, env)
    else:
        _launch_gui(repo, env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
