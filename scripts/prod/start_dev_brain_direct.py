import os
import sys
from pathlib import Path

# Use this checkout unless the developer explicitly selects another local root.
main_root = Path(os.environ.get("CHAD_MAIN_ROOT", Path(__file__).resolve().parents[2])).resolve()
env_file = main_root / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            os.environ[k] = v

os.environ["PORT"] = "8788"
os.environ["CHAD_MODE"] = "cloud"
os.environ["CHAD_DEV_WORKTREE"] = "1"
os.environ["CHAD_MAIN_ROOT"] = str(main_root)
os.environ["CHAD_LOCAL_DB_PATH"] = str(main_root / "data" / "local_chad.db")
os.environ["CHAD_SECRETS_PATH"] = str(main_root / "config" / "secrets.yaml")
os.environ["CHAD_DAEMONS_ENABLED"] = "false"

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.environ["PYTHONPATH"] = str(root_dir)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.remote.server:app",
        host="0.0.0.0",
        port=8788,
        reload=False,
        ws_ping_interval=20.0,
        ws_ping_timeout=60.0,
    )
