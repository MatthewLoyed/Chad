"""Dev-only Brain launcher — auto-restarts the process on every src/ or config/ save.

Runs the SAME ``src.remote.server:app`` used by prod, just under uvicorn's file-watch
reloader instead of a plain ``python server.py`` invocation. Removes the "did I actually
restart the dev server" ambiguity during active development: edit a file, save, the
process restarts itself in ~1-2s and picks up the change automatically.

Only wired into launch_dev.ps1 (port 8788, dev worktree). Prod (chad_supervisor.ps1,
port 8767) always runs server.py directly and must never use this — reload mode restarts
the whole process on every file change, which is exactly what PRODUCTION_BRAIN.md forbids
doing to the live server.
"""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
os.environ["PYTHONPATH"] = str(ROOT_DIR)
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import uvicorn


def _ensure_shared_local_db() -> None:
    """Point at main's SQLite when running under the dev worktree.

    launch_dev.ps1 sets CHAD_LOCAL_DB_PATH; this is a belt-and-suspenders default
    so a manual ``python scripts/prod/run_dev_brain.py`` from AIAssistant-dev
    still hits prod's Drive/activity index instead of an empty local copy.
    """
    if os.environ.get("CHAD_LOCAL_DB_PATH"):
        return
    main = os.environ.get("CHAD_MAIN_ROOT")
    if not main:
        return
    candidate = Path(main) / "data" / "local_chad.db"
    if candidate.is_file():
        os.environ["CHAD_LOCAL_DB_PATH"] = str(candidate)


if __name__ == "__main__":
    _ensure_shared_local_db()
    should_reload = os.getenv("CHAD_DEV_RELOAD", "true").lower() in ("1", "true", "yes")
    uvicorn.run(
        "src.remote.server:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8788)),
        reload=should_reload,
        reload_dirs=["src", "config"] if should_reload else None,
        ws_ping_interval=20.0,
        ws_ping_timeout=60.0,
    )
