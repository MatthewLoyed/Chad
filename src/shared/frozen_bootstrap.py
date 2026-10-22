"""Frozen-build bootstrap for the packaged Chad GUI (``Chad.exe``).

When the desktop Limb is shipped as a PyInstaller one-folder binary, ``__file__``
and ``sys._MEIPASS`` point inside the bundle — but the repo that holds
``config/``, ``data/``, profiles and the always-on Brain still lives on disk.
This module resolves that real repo (``CHAD_HOME``), exports it, and ``chdir``s
into it so every ``os.getcwd()``- and project-root-relative path keeps working
exactly as it does from a source checkout.

It is a no-op when running from source (``sys.frozen`` is not set), so importing
and calling it unconditionally at the top of ``main_gui.py`` is safe.
"""

import os
import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    """True when running inside a PyInstaller (or similar) frozen build."""
    return bool(getattr(sys, "frozen", False))


def resolve_chad_home() -> Optional[str]:
    """Locate the real Chad repo root for a frozen build.

    Resolution order (first hit wins):
      1. ``CHAD_HOME`` env var, if it points at a real directory.
      2. A ``chad_home.txt`` file next to the executable (written at build /
         install time with the absolute repo path).
      3. The executable's own directory, as a last resort.

    Returns ``None`` when not frozen — callers should leave the environment
    untouched in that case.
    """
    if not is_frozen():
        return None

    env = os.environ.get("CHAD_HOME")
    if env and Path(env).is_dir():
        return str(Path(env).absolute())

    exe_dir = Path(sys.executable).resolve().parent
    marker = exe_dir / "chad_home.txt"
    if marker.exists():
        try:
            # utf-8-sig tolerates a BOM (PowerShell's Set-Content -Encoding utf8
            # writes one); strip whitespace and any stray BOM char defensively.
            candidate = marker.read_text(encoding="utf-8-sig").strip().lstrip("﻿")
        except OSError:
            candidate = ""
        if candidate and Path(candidate).is_dir():
            return str(Path(candidate).absolute())

    return str(exe_dir)


def bootstrap_frozen() -> Optional[str]:
    """Export ``CHAD_HOME`` and ``chdir`` into it when frozen. Returns the home."""
    home = resolve_chad_home()
    if not home:
        return None
    os.environ["CHAD_HOME"] = home
    try:
        os.chdir(home)
    except OSError:
        pass
    return home


def redirect_stdio_for_frozen_windowed() -> Optional[Path]:
    """Redirect stdout/stderr to a log file for a frozen, windowed build.

    PyInstaller's windowed (``console=False``) bootloader leaves stdout/stderr
    unusable: bare ``print()`` calls raise ``AttributeError`` (nothing catches
    it), and every ``logging`` call goes through ``StreamHandler.handleError``,
    which silently drops the record because ``sys.stderr`` is falsy — so
    real failures produce zero output anywhere a user or developer can see.
    Redirecting to a real file makes both cases work and gives the user
    something to attach when reporting a blank/broken window.

    No-op (returns ``None``) when not frozen — console builds already have a
    working stdout.
    """
    if not is_frozen():
        return None
    from src.shared.paths import get_log_dir

    log_path = get_log_dir() / "gui_stdio.log"
    try:
        log_file = open(log_path, "a", buffering=1, encoding="utf-8")
    except OSError:
        return None
    sys.stdout = log_file
    sys.stderr = log_file
    return log_path


def bundled_web_dist() -> Optional[Path]:
    """Path to the React build bundled inside the frozen app, if present.

    PyInstaller unpacks ``datas`` under ``sys._MEIPASS``; the spec ships the web
    build at ``web/dist``. Returns ``None`` when not frozen or not bundled.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    candidate = Path(base) / "web" / "dist" / "index.html"
    return candidate if candidate.exists() else None
