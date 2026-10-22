"""Declarative event → buddy animation rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Type

from src.application.events.event_bus import Event

EventMatcher = Callable[[Event], bool]
CueResolver = str | Callable[[Event], str | None]
MetaResolver = Callable[[Event], dict[str, Any]]


@dataclass
class BuddyRule:
    """Maps EventBus events to sustained modes and/or one-shot animation cues.

    Add rules via ``BuddyRegistry.register`` or ``BuddyEngine.register_rule``.
    Multiple rules may fire for a single event; higher ``priority`` wins for
    mode changes, and the newest cue replaces an expired one.
    """

    name: str
    events: tuple[Type[Event], ...]
    match: EventMatcher | None = None
    mode: str | None = None
    cue: CueResolver | None = None
    cue_duration_s: float = 2.0
    priority: int = 0
    clear_cues: bool = False
    meta_from_event: MetaResolver | None = None

    def applies_to(self, event: Event) -> bool:
        if not isinstance(event, self.events):
            return False
        if self.match is not None and not self.match(event):
            return False
        return True

    def resolve_cue(self, event: Event) -> str | None:
        if self.cue is None:
            return None
        if callable(self.cue):
            return self.cue(event)
        return self.cue

    def resolve_meta(self, event: Event) -> dict[str, Any]:
        if self.meta_from_event is None:
            return {}
        return dict(self.meta_from_event(event))


class BuddyRegistry:
    """Ordered collection of buddy rules — the extension point for new animations."""

    def __init__(self, rules: Iterable[BuddyRule] | None = None) -> None:
        self._rules: list[BuddyRule] = []
        if rules:
            self.extend(rules)

    @property
    def rules(self) -> tuple[BuddyRule, ...]:
        return tuple(self._rules)

    def register(self, rule: BuddyRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def extend(self, rules: Iterable[BuddyRule]) -> None:
        for rule in rules:
            self.register(rule)

    def rules_for(self, event: Event) -> list[BuddyRule]:
        return [rule for rule in self._rules if rule.applies_to(event)]
