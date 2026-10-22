"""
Debug Helper - Centralized Developer Debug Control
Provides a simple toggle for enabling/disabling all debug output.
"""

import os
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_dev_debug_setting() -> bool:
    """
    Load the dev_debug setting from config.yaml.
    Cached to avoid repeated file reads.
    
    Returns:
        True if debug output is enabled, False otherwise.
    """
    try:
        import yaml
        # The file is in src/shared/debug_helper.py
        # Root config is in config/config.yaml
        config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                return config.get('dev_debug', False)  # Default to False (silent) now that we're on laptop
    except Exception:
        pass
    return True  # Default to True on any error


def is_debug_enabled() -> bool:
    """
    Check if developer debug mode is enabled.
    
    Returns:
        True if debug output should be shown, False otherwise.
    """
    return _load_dev_debug_setting()


import logging

_logger = logging.getLogger("System")

def debug_print(*args, **kwargs) -> None:
    """
    Log debug message only if dev_debug is enabled in config.yaml.
    Drop-in replacement for print("DEBUG: ...") calls.
    
    Usage:
        from src.shared.debug_helper import debug_print
        debug_print("DEBUG: Something happened")
    """
    if is_debug_enabled():
        # Combine args into a single message string for the logger
        message = " ".join(map(str, args))
        _logger.debug(message)


def clear_debug_cache() -> None:
    """
    Clear the cached dev_debug setting.
    Call this if config.yaml is modified at runtime.
    """
    _load_dev_debug_setting.cache_clear()
