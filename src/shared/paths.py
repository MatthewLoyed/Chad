"""
Centralized Path Management Utility
Provides dynamic path resolution, project root discovery, and environment variable expansion.
Ensures the application remains portable and consumer-ready.
"""

import os
from pathlib import Path

def get_project_root() -> Path:
    """
    Returns the absolute path to the project root directory.

    Resolution order:
      1. ``CHAD_HOME`` environment variable, when set. This is required when the
         GUI runs as a frozen PyInstaller binary (``Chad.exe``): there ``__file__``
         points inside the bundle, not at the repo that holds config/, data/ and
         profiles. The frozen bootstrap in ``main_gui.py`` sets ``CHAD_HOME`` so
         the packaged Limb still reads the real always-on Brain's repo.
      2. Otherwise, derive from this file's location (src/shared/paths.py is 3
         levels deep from the repo root) — the normal source-checkout path.
    """
    home = os.environ.get("CHAD_HOME")
    if home:
        return Path(home).absolute()
    # src/shared/paths.py -> shared -> src -> root
    return Path(__file__).parent.parent.parent.absolute()

def resolve_path(path_str: str) -> str:
    """
    Resolves a path string by expanding environment variables and 
    converting to absolute path where necessary.
    
    Example: '%USERPROFILE%/Desktop' -> 'C:/Users/name/Desktop'
    Example: 'data/logs' -> '[PROJECT_ROOT]/data/logs'
    """
    if not path_str:
        return ""
    
    # Expand environment variables (%VAR% or $VAR)
    expanded = os.path.expandvars(path_str)
    
    path = Path(expanded)
    
    # If it's not absolute, make it relative to project root
    if not path.is_absolute():
        path = get_project_root() / path
        
    return str(path)

def get_log_dir() -> Path:
    """Returns the dedicated log directory within the project."""
    # Updated to docs/logs as per master-rules.md
    log_dir = get_project_root() / "docs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

def get_config_dir() -> Path:
    """Returns the central configuration directory."""
    return get_project_root() / "config"

def get_user_home() -> Path:
    """Returns the current user's home directory."""
    return Path.home()
