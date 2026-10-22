"""
Modular nudge framework — runtime watchdogs over a single LLM generation.

A Nudge observes streaming output (reasoning tokens, tool calls, etc.) for an
undesirable pattern and, when triggered, supplies a corrective message that the
caller resends with thinking disabled. New nudge types plug in by subclassing
`Nudge` and adding an instance to the list passed to `NudgeManager`.
"""

from .base import Nudge, NudgeContext, NudgeResult
from .manager import NudgeManager
from .narration_guard import NarrationWithoutActionGuard
from .reasoning_runaway_guard import ReasoningRunawayGuard

__all__ = [
    "Nudge",
    "NudgeContext",
    "NudgeResult",
    "NudgeManager",
    "NarrationWithoutActionGuard",
    "ReasoningRunawayGuard",
]
