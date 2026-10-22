"""
TurnConcurrencyGate — Priority-Aware Inference & Turn Concurrency Gate

Prevents concurrent execution of background turns (Gmail webhooks, cron, heartbeats)
while interactive user turns are active, and guarantees mutual exclusion across turns
when local models / single GPUs are active to prevent VRAM thrashing and model collisions.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from src.shared.logger import setup_logger

logger = setup_logger("TurnConcurrencyGate")


class TurnConcurrencyGate:
    """Coordinates execution between interactive user turns and background daemon turns.

    Priorities:
    - User (Interactive) turns: HIGH priority.
    - Background (Daemon/Webhook/Cron) turns: LOW priority, strictly queued behind user turns.

    When local models are active (is_local=True), enforces single-flight execution (mutex)
    so LM Studio / local GPU never handles concurrent graph runs or model swaps.
    """

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._active_user_turns: int = 0
        self._waiting_user_turns: int = 0
        self._active_background_turns: int = 0
        self._local_executing: bool = False

    @asynccontextmanager
    async def turn_guard(
        self,
        is_background: bool,
        is_local: bool = False,
        session_id: str = "default",
    ) -> AsyncIterator[None]:
        """Context manager guarding turn entry based on priority and model locality."""
        if is_background:
            async with self._cond:
                # Background turns wait if any user turn is active, waiting, or another turn is holding local execution
                while (
                    self._active_user_turns > 0
                    or self._waiting_user_turns > 0
                    or self._active_background_turns > 0
                    or (is_local and self._local_executing)
                ):
                    logger.info(
                        f"[ CONCURRENCY_GATE ] Background turn '{session_id}' queued "
                        f"(active_user={self._active_user_turns}, waiting_user={self._waiting_user_turns}, "
                        f"active_bg={self._active_background_turns}, local_busy={self._local_executing})"
                    )
                    await self._cond.wait()

                self._active_background_turns += 1
                if is_local:
                    self._local_executing = True
                logger.info(f"[ CONCURRENCY_GATE ] Background turn '{session_id}' acquired execution slot.")

            try:
                yield
            finally:
                async with self._cond:
                    self._active_background_turns = max(0, self._active_background_turns - 1)
                    if is_local:
                        self._local_executing = False
                    self._cond.notify_all()
                    logger.debug(f"[ CONCURRENCY_GATE ] Background turn '{session_id}' released slot.")
        else:
            # Interactive User Turn
            async with self._cond:
                self._waiting_user_turns += 1

            try:
                async with self._cond:
                    # User turn only waits if local model is busy with another turn
                    while is_local and self._local_executing:
                        logger.info(
                            f"[ CONCURRENCY_GATE ] User turn '{session_id}' waiting for active turn to release local model "
                            f"(active_user={self._active_user_turns}, active_bg={self._active_background_turns})"
                        )
                        await self._cond.wait()

                    self._waiting_user_turns = max(0, self._waiting_user_turns - 1)
                    self._active_user_turns += 1
                    if is_local:
                        self._local_executing = True
                    logger.info(f"[ CONCURRENCY_GATE ] User turn '{session_id}' acquired execution slot.")

                yield
            finally:
                async with self._cond:
                    self._active_user_turns = max(0, self._active_user_turns - 1)
                    if is_local:
                        self._local_executing = False
                    self._cond.notify_all()
                    logger.debug(f"[ CONCURRENCY_GATE ] User turn '{session_id}' released slot.")

    def reset_for_tests(self) -> None:
        """Reset internal counters for testing isolation."""
        self._active_user_turns = 0
        self._waiting_user_turns = 0
        self._active_background_turns = 0
        self._local_executing = False
