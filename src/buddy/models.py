"""Data models for the desktop buddy event → animation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


BUDDY_MODES = frozenset({"idle", "listening", "thinking", "working", "error"})


@dataclass(frozen=True)
class BuddyCue:
    """One-shot animation overlay (e.g. success flash after email sent)."""

    animation: str
    rule_name: str
    until: float
    priority: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "animation": self.animation,
            "rule": self.rule_name,
            "until": self.until,
            "priority": self.priority,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class BuddyComment:
    """Ambient observation shown in the desktop pet speech bubble."""

    text: str
    rule_name: str
    until: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "rule": self.rule_name,
            "until": self.until,
        }


@dataclass
class BuddySnapshot:
    """Serializable buddy state polled by the desktop pet client."""

    mode: str = "idle"
    cue: BuddyCue | None = None
    comment: BuddyComment | None = None
    active_tools: int = 0
    turn_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            # Legacy key — same value as mode for older clients.
            "state": self.mode,
            "mode": self.mode,
            "active_tools": self.active_tools,
            "turn_active": self.turn_active,
        }
        if self.cue is not None:
            payload["cue"] = self.cue.to_dict()
        if self.comment is not None:
            payload["comment"] = self.comment.to_dict()
        return payload
