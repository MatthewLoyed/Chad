"""
Unit tests for SessionLockManager.

Covers:
- get() returns an asyncio.Lock
- get() with the same key returns the same lock instance (idempotent)
- get() with different keys returns different locks
- has() reflects whether a key has been allocated
- active_sessions() lists allocated keys
- Lock is actually functional (can be acquired / released)
"""

import sys
from pathlib import Path
import asyncio
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.conversation.session_lock_manager import SessionLockManager


# ---------------------------------------------------------------------------
# Basic get / identity
# ---------------------------------------------------------------------------

def test_get_returns_lock():
    manager = SessionLockManager()
    lock = manager.get("session-a")
    assert isinstance(lock, asyncio.Lock)


def test_get_same_key_same_lock():
    manager = SessionLockManager()
    lock1 = manager.get("session-a")
    lock2 = manager.get("session-a")
    assert lock1 is lock2


def test_get_different_keys_different_locks():
    manager = SessionLockManager()
    lock_a = manager.get("session-a")
    lock_b = manager.get("session-b")
    assert lock_a is not lock_b


# ---------------------------------------------------------------------------
# has() + active_sessions()
# ---------------------------------------------------------------------------

def test_has_false_before_get():
    manager = SessionLockManager()
    assert not manager.has("session-x")


def test_has_true_after_get():
    manager = SessionLockManager()
    manager.get("session-x")
    assert manager.has("session-x")


def test_active_sessions_empty_initially():
    manager = SessionLockManager()
    assert manager.active_sessions() == []


def test_active_sessions_lists_keys():
    manager = SessionLockManager()
    manager.get("s1")
    manager.get("s2")
    sessions = manager.active_sessions()
    assert "s1" in sessions
    assert "s2" in sessions
    assert len(sessions) == 2


# ---------------------------------------------------------------------------
# Lock is functional (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_is_acquirable():
    manager = SessionLockManager()
    lock = manager.get("session-a")
    async with lock:
        assert lock.locked()
    assert not lock.locked()


@pytest.mark.asyncio
async def test_lock_serialises_concurrent_access():
    """Two concurrent coroutines using the same session key must not overlap."""
    manager = SessionLockManager()
    results = []

    async def worker(name: str):
        async with manager.get("shared-session"):
            results.append(f"{name}-enter")
            await asyncio.sleep(0.01)
            results.append(f"{name}-exit")

    await asyncio.gather(worker("A"), worker("B"))

    # Verify no interleaving: A's exit must precede B's enter, or vice versa
    if results[0] == "A-enter":
        assert results == ["A-enter", "A-exit", "B-enter", "B-exit"]
    else:
        assert results == ["B-enter", "B-exit", "A-enter", "A-exit"]
