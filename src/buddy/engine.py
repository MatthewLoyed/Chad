"""EventBus subscriber that resolves buddy modes and optional animation cues."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from src.application.events import (
    ErrorOccurred,
    ToolCompleted,
    ToolStarted,
    TurnCompleted,
    TurnStartEvent,
    TurnStarted,
)
from src.application.events.events import TurnCompleteEvent
from src.buddy.models import BUDDY_MODES, BuddyComment, BuddyCue, BuddySnapshot
from src.buddy.rules import BuddyRegistry, BuddyRule
from src.shared.logger import setup_logger

logger = setup_logger("BuddyEngine")

Listener = Callable[[dict[str, Any]], None]

_DEFAULT_RULES: tuple[BuddyRule, ...] = (
    BuddyRule(
        name="turn_started",
        events=(TurnStarted, TurnStartEvent),
        mode="thinking",
        priority=10,
    ),
    BuddyRule(
        name="turn_completed",
        events=(TurnCompleted, TurnCompleteEvent),
        mode="idle",
        clear_cues=True,
        priority=10,
    ),
    BuddyRule(
        name="tool_started",
        events=(ToolStarted,),
        mode="working",
        priority=20,
    ),
    BuddyRule(
        name="tool_success",
        events=(ToolCompleted,),
        match=lambda e: e.success,
        cue="success",
        cue_duration_s=1.5,
        priority=30,
    ),
    BuddyRule(
        name="tool_failed",
        events=(ToolCompleted,),
        match=lambda e: not e.success,
        mode="error",
        cue="error",
        cue_duration_s=3.0,
        priority=40,
    ),
    BuddyRule(
        name="error_occurred",
        events=(ErrorOccurred,),
        mode="error",
        cue="error",
        cue_duration_s=3.0,
        priority=40,
    ),
)


class BuddyEngine:
    """Maps Brain EventBus activity to buddy modes, cues, comments, and SSE push."""

    def __init__(self, event_bus, registry: BuddyRegistry | None = None) -> None:
        self._lock = threading.Lock()
        self._listener_lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._registry = registry or BuddyRegistry(_DEFAULT_RULES)
        self._mode = "idle"
        self._cue: BuddyCue | None = None
        self._comment: BuddyComment | None = None
        self._active_tools = 0
        self._turn_active = False
        self._listening = False
        self._error_until = 0.0
        self._error_timer: threading.Timer | None = None
        self._subscribed: set[type] = set()
        self._event_bus = event_bus
        self._subscribe(event_bus)
        logger.info("[ BUDDY ] Engine subscribed to EventBus (%d rules).", len(self._registry.rules))

    def subscribe(self, callback: Listener) -> Callable[[], None]:
        """Register for push updates (SSE). Returns an unsubscribe callable."""
        with self._listener_lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._listener_lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    @staticmethod
    def sse_line(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def _broadcast(self) -> None:
        payload = self.get_state()
        with self._listener_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(payload)
            except Exception as exc:
                logger.debug("[ BUDDY ] listener error: %s", exc)

    def _schedule_error_clear(self, duration_s: float = 3.05) -> None:
        if self._error_timer:
            self._error_timer.cancel()

        def _clear() -> None:
            with self._lock:
                if time.monotonic() >= self._error_until:
                    self._error_until = 0.0
            self._broadcast()

        self._error_timer = threading.Timer(duration_s, _clear)
        self._error_timer.daemon = True
        self._error_timer.start()

    @property
    def registry(self) -> BuddyRegistry:
        return self._registry

    def register_rule(self, rule: BuddyRule) -> None:
        """Add a rule at runtime (plugins, tests, config loaders)."""
        self._registry.register(rule)
        if self._event_bus is not None:
            for event_type in rule.events:
                if event_type in self._subscribed:
                    continue
                self._subscribed.add(event_type)
                self._event_bus.subscribe(event_type, self._on_event)

    def set_comment(self, text: str, rule_name: str, display_s: float = 45.0) -> None:
        with self._lock:
            self._comment = BuddyComment(
                text=text,
                rule_name=rule_name,
                until=time.monotonic() + display_s,
            )
        self._broadcast()

    def clear_comment(self) -> None:
        with self._lock:
            self._comment = None

    def get_snapshot(self) -> BuddySnapshot:
        with self._lock:
            return BuddySnapshot(
                mode=self._resolve_mode_locked(),
                cue=self._active_cue_locked(),
                comment=self._active_comment_locked(),
                active_tools=self._active_tools,
                turn_active=self._turn_active,
            )

    def get_state(self) -> dict[str, Any]:
        """Backward-compatible dict for ``GET /buddy/state``."""
        return self.get_snapshot().to_dict()

    def set_listening(self, active: bool) -> None:
        """Voice mode signal: wake word fired (True) or the turn began (False).

        Externally driven (the Limb sends a VOICE_STATE message) rather than via an
        EventBus rule, since the wake word is heard client-side before any turn.
        """
        before = self.get_state()
        with self._lock:
            self._listening = bool(active)
        if self.get_state() != before:
            self._broadcast()

    def _subscribe(self, event_bus) -> None:
        for rule in self._registry.rules:
            for event_type in rule.events:
                if event_type in self._subscribed:
                    continue
                self._subscribed.add(event_type)
                event_bus.subscribe(event_type, self._on_event)

    def _on_event(self, event) -> None:
        matching = self._registry.rules_for(event)
        if not matching:
            return

        before = self.get_state()
        error_scheduled = False
        with self._lock:
            self._apply_counters_locked(event)
            for rule in matching:
                self._apply_rule_locked(rule, event)
                if rule.mode == "error":
                    error_scheduled = True
        if error_scheduled:
            self._schedule_error_clear()
        after = self.get_state()
        if before != after:
            self._broadcast()

    def _apply_counters_locked(self, event) -> None:
        if isinstance(event, (TurnStarted, TurnStartEvent)):
            self._turn_active = True
        elif isinstance(event, (TurnCompleted, TurnCompleteEvent)):
            self._turn_active = False
            self._active_tools = 0
        elif isinstance(event, ToolStarted):
            self._active_tools += 1
        elif isinstance(event, ToolCompleted):
            self._active_tools = max(0, self._active_tools - 1)

    def _apply_rule_locked(self, rule: BuddyRule, event) -> None:
        if rule.clear_cues:
            self._cue = None

        if rule.mode is not None:
            if rule.mode in BUDDY_MODES:
                self._mode = rule.mode
            if rule.mode == "error":
                until = time.monotonic() + rule.cue_duration_s
                self._error_until = max(self._error_until, until)

        animation = rule.resolve_cue(event)
        if animation:
            current_priority = self._cue.priority if self._cue else -1
            if rule.priority < current_priority:
                return
            until = time.monotonic() + rule.cue_duration_s
            self._cue = BuddyCue(
                animation=animation,
                rule_name=rule.name,
                until=until,
                priority=rule.priority,
                meta=rule.resolve_meta(event),
            )

    def _resolve_mode_locked(self) -> str:
        now = time.monotonic()
        if now < self._error_until:
            return "error"
        if self._mode == "error":
            self._mode = "idle"
        # Voice mode: brief pre-turn cue (wake word heard). Cleared by the Limb once
        # the turn starts, so the normal turn/tool modes take over from there.
        if self._listening:
            return "listening"
        if self._active_tools > 0:
            return "working"
        if self._turn_active:
            return "thinking"
        return self._mode if self._mode in BUDDY_MODES else "idle"

    def _active_cue_locked(self) -> BuddyCue | None:
        if self._cue is None:
            return None
        if time.monotonic() >= self._cue.until:
            self._cue = None
            return None
        return self._cue

    def _active_comment_locked(self) -> BuddyComment | None:
        if self._comment is None:
            return None
        if time.monotonic() >= self._comment.until:
            self._comment = None
            return None
        return self._comment
