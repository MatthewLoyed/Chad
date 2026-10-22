"""Ambient observer — timer/event → capture → vision comment → speech bubble."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import TYPE_CHECKING, Any

from src.application.events import TurnCompleted
from src.application.events.events import TurnCompleteEvent
from src.buddy.capture import capture_desktop
from src.buddy.triggers import BuddyTrigger, BuddyTriggerRegistry
from src.buddy.vision import BuddyVisionClient
from src.remote.daemon_bridge import DaemonOfflineError
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

if TYPE_CHECKING:
    from src.buddy.engine import BuddyEngine

logger = setup_logger("BuddyObserver")

_DEFAULT_TRIGGERS: tuple[BuddyTrigger, ...] = (
    BuddyTrigger(name="timer", interval_s=1800.0, cooldown_s=1800.0),
)


class BuddyObserver:
    """Proactive desk comments for the desktop buddy pet."""

    def __init__(
        self,
        event_bus,
        engine: BuddyEngine,
        config: dict[str, Any] | None = None,
        *,
        vision_client: BuddyVisionClient | None = None,
        trigger_registry: BuddyTriggerRegistry | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._engine = engine
        self._config = config if config is not None else (ConfigManager.get("buddy.observer") or {})
        self._vision = vision_client or BuddyVisionClient()
        self._triggers = trigger_registry or BuddyTriggerRegistry(_DEFAULT_TRIGGERS)
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._observe_lock = asyncio.Lock()
        self._last_observe_at = 0.0
        self._last_frame_hash: str | None = None
        self._subscribed: set[type] = set()

        interval_min = float(self._config.get("interval_minutes", 30))
        if self._config.get("interval_minutes") is not None:
            for trigger in self._triggers.triggers:
                if trigger.interval_s is not None:
                    trigger.interval_s = interval_min * 60.0
                    trigger.cooldown_s = interval_min * 60.0

        if self._config.get("trigger_on_turn_complete", False):
            self._triggers.register(
                BuddyTrigger(
                    name="turn_complete",
                    events=(TurnCompleted, TurnCompleteEvent),
                    cooldown_s=float(self._config.get("event_cooldown_s", 1800.0)),
                )
            )

    @property
    def triggers(self) -> BuddyTriggerRegistry:
        return self._triggers

    def register_trigger(self, trigger: BuddyTrigger) -> None:
        self._triggers.register(trigger)
        if self._running:
            self._subscribe_trigger(trigger)

    async def start(self) -> None:
        if self._running:
            return
        if not self._config.get("enabled", False):
            logger.info("[ BUDDY ] Observer disabled in config.")
            return

        self._running = True
        for trigger in self._triggers.triggers:
            self._subscribe_trigger(trigger)
            if trigger.interval_s:
                self._tasks.append(asyncio.create_task(self._timer_loop(trigger)))

        logger.info(
            "[ BUDDY ] Observer started (%d triggers, model=%s).",
            len(self._triggers.triggers),
            self._vision.model_id,
        )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    def _subscribe_trigger(self, trigger: BuddyTrigger) -> None:
        for event_type in trigger.events:
            if event_type in self._subscribed:
                continue
            self._subscribed.add(event_type)
            self._event_bus.subscribe(event_type, self._on_event)

    def _on_event(self, event) -> None:
        matching = [t for t in self._triggers.triggers if t.applies_to(event)]
        for trigger in matching:
            asyncio.create_task(self.observe(trigger.name))

    async def _timer_loop(self, trigger: BuddyTrigger) -> None:
        assert trigger.interval_s is not None
        # Stagger first run so server boot isn't immediate capture
        await asyncio.sleep(min(60.0, trigger.interval_s))
        while self._running:
            await self.observe(trigger.name)
            await asyncio.sleep(trigger.interval_s)

    async def observe(self, trigger_name: str) -> None:
        async with self._observe_lock:
            now = time.monotonic()
            cooldown = float(self._config.get("cooldown_s", 1800.0))
            if now - self._last_observe_at < cooldown:
                logger.debug("[ BUDDY ] Observer cooldown — skipping (%s).", trigger_name)
                return

            try:
                grab = await capture_desktop(all_screens=bool(self._config.get("all_screens", True)))
            except DaemonOfflineError:
                logger.warning("[ BUDDY ] Hands offline — skipping observation.")
                return
            except Exception as exc:
                logger.error("[ BUDDY ] Capture failed: %s", exc)
                return

            if self._config.get("skip_unchanged", True):
                frame_hash = hashlib.sha256(grab.image_b64[:8192].encode()).hexdigest()
                if frame_hash == self._last_frame_hash:
                    logger.debug("[ BUDDY ] Screen unchanged — skipping vision call.")
                    return
                self._last_frame_hash = frame_hash

            try:
                comment = await self._vision.comment_on_screen(grab.image_b64)
            except Exception as exc:
                logger.error("[ BUDDY ] Vision comment failed: %s", exc)
                return

            display_s = float(self._config.get("comment_display_s", 45.0))
            self._engine.set_comment(comment, trigger_name, display_s)
            self._last_observe_at = now
            logger.info("[ BUDDY ] Observation [%s]: %s", trigger_name, comment[:60])
