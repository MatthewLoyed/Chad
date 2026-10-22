"""Eval mode is not a live user turn — residency & scheduling policy (#632).

All harness lanes (dry-run / live / workflow) tag ``platform="eval"``. That is an
intentional fork from production interactive turns:

1. **Exclusive owner of the machine (dev).** ``eval_turn_guard`` blocks desktop /
   cron / other non-eval turns at orchestrator entry while any eval is in flight.
   Concurrent eval workers share the gate and do not block each other.
2. **Interactive model-lifecycle priority** — not background. Evals do not wait
   behind ``interactive_active_window_s``; they load/evict like a foreground turn.
3. **Keep the 9B selector resident** across cases (``llm_selector`` skips
   unload-after-select while this gate is held). Production still unloads after
   select so the main chat model keeps VRAM.
4. **Skip production residency choreography** that would thrash VRAM for the
   harness (``_is_eval_turn`` → no post-turn ``schedule_warm`` of the main model
   on SpyAdapter dry-runs; intent classify skipped; etc.).

Live/workflow cases stay **sequential by design** (subagents / VRAM). Dry-run may
parallelize tool-selection workers. Real Limb clients never send ``platform=eval``
(they default to ``gui``).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

_depth = 0
_condition: Optional[asyncio.Condition] = None


def _cond() -> asyncio.Condition:
    global _condition
    if _condition is None:
        _condition = asyncio.Condition()
    return _condition


def eval_turns_active() -> int:
    """How many eval turns are currently inside ``eval_turn_guard``."""
    return _depth


def reset_for_tests() -> None:
    """Drop gate state between unit tests (same event loop)."""
    global _depth, _condition
    _depth = 0
    _condition = None


@asynccontextmanager
async def eval_turn_guard(platform: Optional[str]) -> AsyncIterator[None]:
    """Exclusive for ``platform==\"eval\"``; everyone else waits while evals run."""
    global _depth
    cond = _cond()
    if platform == "eval":
        async with cond:
            _depth += 1
            if _depth == 1:
                logger.info(
                    "[ EVAL_EXCLUSIVITY ] Eval turn started — non-eval turns will wait"
                )
        try:
            yield
        finally:
            async with cond:
                _depth = max(0, _depth - 1)
                if _depth == 0:
                    logger.info(
                        "[ EVAL_EXCLUSIVITY ] Eval turns idle — releasing non-eval waiters"
                    )
                cond.notify_all()
        return

    async with cond:
        while _depth > 0:
            logger.info(
                "[ EVAL_EXCLUSIVITY ] Non-eval turn waiting (%d eval turn(s) active)",
                _depth,
            )
            await cond.wait()
    yield
