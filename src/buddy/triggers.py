"""Plug-and-play triggers for the buddy ambient observer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Type

from src.application.events.event_bus import Event

EventMatcher = Callable[[Event], bool]


@dataclass
class BuddyTrigger:
    """Fires the observer on a timer and/or EventBus events."""

    name: str
    interval_s: float | None = None
    events: tuple[Type[Event], ...] = field(default_factory=tuple)
    match: EventMatcher | None = None
    cooldown_s: float = 1800.0

    def applies_to(self, event: Event) -> bool:
        if self.events and not isinstance(event, self.events):
            return False
        if self.match is not None and not self.match(event):
            return False
        return bool(self.events)


class BuddyTriggerRegistry:
    def __init__(self, triggers: Iterable[BuddyTrigger] | None = None) -> None:
        self._triggers: list[BuddyTrigger] = []
        if triggers:
            self.extend(triggers)

    @property
    def triggers(self) -> tuple[BuddyTrigger, ...]:
        return tuple(self._triggers)

    def register(self, trigger: BuddyTrigger) -> None:
        self._triggers.append(trigger)

    def extend(self, triggers: Iterable[BuddyTrigger]) -> None:
        for trigger in triggers:
            self.register(trigger)
