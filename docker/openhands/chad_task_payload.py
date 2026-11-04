"""Read a mounted task payload; retain the original environment-only protocol."""
import os
from pathlib import Path


def read_task():
    path = os.environ.get("CHAD_TASK_FILE")
    return Path(path).read_text(encoding="utf-8") if path else os.environ.get("CHAD_TASK", "")
