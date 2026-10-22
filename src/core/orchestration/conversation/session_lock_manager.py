"""
SessionLockManager — Per-Session asyncio.Lock Allocation

Extracted from ConversationOrchestrator to satisfy the single-responsibility
principle.  Provides a simple get-or-create lock registry keyed by an
arbitrary string session identifier.

The locks are used to serialise concurrent turns that share the same session
(i.e. same user conversation), ensuring only one turn runs at a time per
session while allowing truly independent sessions to proceed in parallel.
"""

import asyncio
from typing import Dict

from src.shared.logger import setup_logger

logger = setup_logger("SessionLockManager")


class SessionLockManager:
    """Get-or-create an :class:`asyncio.Lock` for a given session key.

    Locks are created lazily on first access and retained for the lifetime
    of the manager instance (which is tied to the orchestrator's lifetime).

    Usage::

        manager = SessionLockManager()
        lock = manager.get("scope:account:channel:peer")
        async with lock:
            ...  # only one turn at a time per session
    """

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}

    def get(self, session_id: str) -> asyncio.Lock:
        """Return the lock for *session_id*, creating it if necessary.

        Args:
            session_id: Opaque string key that identifies a session.  In
                practice this is the ``lock_key(session_id, delivery)``
                value produced by :mod:`src.shared.turn_delivery`.

        Returns:
            The :class:`asyncio.Lock` associated with *session_id*.
        """
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
            logger.debug(f"[ LOCK ] Created new session lock for '{session_id}'")
        return self._locks[session_id]

    def has(self, session_id: str) -> bool:
        """Return ``True`` if a lock already exists for *session_id*."""
        return session_id in self._locks

    def active_sessions(self) -> list:
        """Return the list of session IDs that have an allocated lock."""
        return list(self._locks.keys())
