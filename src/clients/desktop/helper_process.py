"""Launch the desktop OS helper without recursively starting the frozen GUI."""
import subprocess
import sys
from src.shared.paths import get_project_root
from src.shared.frozen_bootstrap import is_frozen


def hands_command():
    if is_frozen():
        return [sys.executable, "--hands-daemon"]
    return [sys.executable, str(get_project_root() / "src" / "daemons" / "hands.py")]


def start_hands_daemon():
    return subprocess.Popen(
        hands_command(), cwd=str(get_project_root()),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
