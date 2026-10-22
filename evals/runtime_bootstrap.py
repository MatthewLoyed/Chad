"""Repair missing desktop-session PATH entries for local Windows eval children."""
import os
from pathlib import Path
import shutil


def bootstrap_path() -> dict:
    """Append standard installed launchers only when absent; never edit system PATH."""
    added = []
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates = (("node", program_files / "nodejs"),
                      ("git", program_files / "Git" / "cmd"),
                      ("docker", program_files / "Docker" / "Docker" / "resources" / "bin"))
        for command, directory in candidates:
            if not shutil.which(command) and directory.is_dir():
                os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(directory)
                added.append(str(directory))
    return {"added": added, "available": {name: shutil.which(name) is not None
            for name in ("node", "npx", "git", "docker")}}
