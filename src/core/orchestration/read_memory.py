"""Per-limb read cache — same Scout/agent turn only (not cross-dispatch).

Use ``fresh_read_cache()`` when a limb starts so repeats of the same file_id
return a short already-retrieved message instead of re-hitting the API.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional

_limb_read_cache_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "limb_read_cache", default=None
)


@contextmanager
def fresh_read_cache() -> Iterator[Dict[str, Any]]:
    cache: Dict[str, Any] = {}
    token = _limb_read_cache_var.set(cache)
    try:
        yield cache
    finally:
        _limb_read_cache_var.reset(token)


def current_read_cache() -> Optional[Dict[str, Any]]:
    return _limb_read_cache_var.get()


def cache_get(key: str) -> Optional[Any]:
    cache = _limb_read_cache_var.get()
    if not cache:
        return None
    return cache.get(key)


def cache_put(key: str, value: Any) -> None:
    cache = _limb_read_cache_var.get()
    if cache is None:
        return
    cache[key] = value
