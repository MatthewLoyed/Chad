"""Base types for the nudge framework. See package docstring in __init__.py."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class NudgeContext:
    """Live state from the latest streaming delta, fed to every Nudge each tick."""

    reasoning_chunk: str = ""
    has_tool_call: bool = False
    has_content: bool = False
    # Visible answer text from this delta (NOT reasoning). Needed by nudges that
    # watch what the model is actually saying rather than how long it is thinking
    # — e.g. catching "I'll go ahead and do X" instead of a tool call.
    content_chunk: str = ""
    # Whether this generation had tools bound at all. A turn with no tools can't
    # be faulted for answering in prose, so action-shaped nudges must stay off.
    tools_available: bool = False


@dataclass
class NudgeResult:
    """What to do when a Nudge fires: cancel the in-flight generation and resend."""

    message: str
    disable_thinking: bool = True
    reason: str = ""


class Nudge(ABC):
    """
    One watchdog rule over a single generation attempt.

    Implementations accumulate state in `observe()` and report a trigger from
    `check()`. `reset()` clears that state for reuse across attempts.
    """

    name: str = "nudge"

    @abstractmethod
    def observe(self, context: NudgeContext) -> None:
        """Update internal state from the latest streaming delta."""

    @abstractmethod
    def check(self) -> Optional[NudgeResult]:
        """Return a NudgeResult if this nudge's trigger condition is met, else None."""

    def reset(self) -> None:
        """Clear accumulated state. Override if a subclass holds more than counters."""
