"""Per-turn scratch state, isolated between concurrent turns.

``TurnCoordinator`` is constructed once per Brain (``system_factory`` /
``remote_factory``) and shared by every session. It nonetheless accumulated a set of
*per-turn* fields directly on ``self`` — ``last_turn_metrics``,
``_last_tool_selection``, ``_current_turn_mode`` and friends. Two turns in flight at
once (a background daemon turn alongside a user turn, a phone and desktop client, a
voice escalation) therefore overwrote each other's state, and whichever finished last
won. ``UIStateSyncer`` would then report the wrong turn's token counts.

The turn pipeline already solved this correctly for its other per-turn values —
``turn_delivery_var``, ``turn_category_var``, ``turn_id_var`` in
:mod:`src.core.orchestration.context` are all ContextVars. These fields simply never
got migrated. This module finishes that job by putting them in one ContextVar-held
object.

Why a ContextVar is the right tool here, specifically:

- Concurrent turns run as separate asyncio tasks, and each task gets its own copy of
  the context — so writes are isolated per turn, which is the bug fix.
- An ``await``ed coroutine shares its caller's context, so a caller still sees the
  values after ``dispatch()`` returns. ``evals/inprocess.py`` and ``text_mode`` read
  ``last_turn_metrics`` exactly that way.
- ``EventBus.publish`` dispatches async handlers with ``asyncio.create_task``, which
  *copies* the context at creation time. A handler spawned during the turn therefore
  observes that turn's scratch. Because ``metrics`` is mutated in place (never
  reassigned mid-turn), later updates are visible through the same dict object.

The var is deliberately **not** reset when a turn ends: leaving it set is what lets
the awaiting caller read the finished turn's results. The next turn overwrites it.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TurnScratch:
    """Mutable state belonging to exactly one turn."""

    metrics: Dict[str, Any] = field(default_factory=dict)
    thinking_events: List[Any] = field(default_factory=list)
    mode: str = "agent"
    is_simple: bool = False
    tool_selection: Dict[str, Any] = field(default_factory=dict)
    bundled_skills: List[str] = field(default_factory=list)
    knowledge_boost: str = ""


turn_scratch_var: ContextVar[Optional[TurnScratch]] = ContextVar(
    "turn_scratch", default=None
)


class ScratchField:
    """Proxy an owner attribute to the in-flight turn's scratch.

    Used so ``TurnCoordinator`` can keep the original attribute names — several
    consumers read them straight off the shared instance — while the values actually
    live in the per-turn ContextVar. One descriptor replaces seven property pairs.

    The owner must expose a ``_scratch`` attribute returning the scratch to use.
    """

    def __init__(self, field: str):
        self._field = field

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return getattr(obj._scratch, self._field)

    def __set__(self, obj, value) -> None:
        setattr(obj._scratch, self._field, value)


def current_scratch() -> Optional[TurnScratch]:
    """The in-flight turn's scratch, or ``None`` outside a turn."""
    return turn_scratch_var.get()


def begin_turn() -> TurnScratch:
    """Install a fresh scratch for a turn starting in this context."""
    scratch = TurnScratch()
    turn_scratch_var.set(scratch)
    return scratch


__all__ = [
    "TurnScratch",
    "ScratchField",
    "turn_scratch_var",
    "current_scratch",
    "begin_turn",
]
