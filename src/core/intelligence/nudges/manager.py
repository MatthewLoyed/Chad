"""Composes multiple Nudge watchdogs over a single streaming generation attempt."""

from typing import List, Optional

from .base import Nudge, NudgeContext, NudgeResult


class NudgeManager:
    """Feeds each streaming delta to every registered nudge; returns the first trigger."""

    def __init__(self, nudges: List[Nudge]):
        self._nudges = nudges

    def observe(
        self,
        reasoning_chunk: str = "",
        has_tool_call: bool = False,
        has_content: bool = False,
        content_chunk: str = "",
        tools_available: bool = False,
    ) -> Optional[NudgeResult]:
        """Update every nudge with the latest delta, then return the first one that trips (if any)."""
        context = NudgeContext(
            reasoning_chunk=reasoning_chunk,
            has_tool_call=has_tool_call,
            has_content=has_content or bool(content_chunk),
            content_chunk=content_chunk,
            tools_available=tools_available,
        )
        for nudge in self._nudges:
            nudge.observe(context)
        for nudge in self._nudges:
            result = nudge.check()
            if result:
                return result
        return None

    def reset(self) -> None:
        for nudge in self._nudges:
            nudge.reset()
