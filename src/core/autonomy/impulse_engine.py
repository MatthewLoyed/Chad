"""
Impulse Engine - Organic Human Pacing & State Machine

Replaces rigid clockwork cron intervals with organic human texting rhythms:
- Dynamic cooldowns with Gaussian/random jitter (2.5 to 5 hours).
- Chill event tolerance (e.g. class / casual study is allowed).
- Post-event check-in spikes (15-45 minutes after workout, class, or church).
- Absolute sleep quiet hours (23:30 to 08:30).
- Permanent deduplication of calendar event check-ins.
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.infrastructure.repositories.state_repository import StateRepository
from src.shared.logger import setup_logger

logger = setup_logger("ImpulseEngine")

KEY_LAST_SPONTANEOUS_TS = "cadence:last_spontaneous_ts"
KEY_CHECKED_EVENTS = "cadence:checked_event_ids"
KEY_CURRENT_COOLDOWN = "cadence:current_cooldown_seconds"

# Quiet sleep window: 11:30 PM to 8:30 AM
QUIET_START_HOUR = 23.5
QUIET_END_HOUR = 8.5

# High-priority quiet events where Matthew should never be interrupted
BLOCKED_EVENT_KEYWORDS = (
    "interview",
    "wedding",
    "date",
    "worship",
    "church",
    "exam",
    "test",
    "presentation",
    "doctor",
    "dentist",
)

# Chill events where a casual text is totally welcome
CHILL_EVENT_KEYWORDS = (
    "class",
    "lecture",
    "study",
    "lab",
    "homework",
    "reading",
)


class ImpulseEngine:
    """Calculates organic spontaneous texting impulse without clockwork rigidity."""

    def __init__(self, state_repo: Optional[StateRepository] = None):
        self._state_repo = state_repo or StateRepository()

    def _get_last_spontaneous_ts(self) -> float:
        return float(self._state_repo.get_value(KEY_LAST_SPONTANEOUS_TS, 0.0) or 0.0)

    def _get_checked_event_ids(self) -> List[str]:
        return self._state_repo.get_value(KEY_CHECKED_EVENTS, []) or []

    def _get_cooldown_seconds(self) -> float:
        val = self._state_repo.get_value(KEY_CURRENT_COOLDOWN, None)
        if val is None:
            # Default initial randomized cooldown between 2.5 and 5 hours
            val = random.uniform(2.5, 5.0) * 3600.0
            self._state_repo.set_value(KEY_CURRENT_COOLDOWN, val)
        return float(val)

    def record_spontaneous_sent(self) -> None:
        """Mark spontaneous outreach as sent and randomize the next refractory cooldown."""
        now = time.time()
        # Next cooldown: 2.5 to 5.0 hours (9,000s to 18,000s)
        next_cooldown = random.uniform(2.5, 5.0) * 3600.0
        self._state_repo.set_value(KEY_LAST_SPONTANEOUS_TS, now)
        self._state_repo.set_value(KEY_CURRENT_COOLDOWN, next_cooldown)
        logger.info(f"[ IMPULSE ] Spontaneous outreach recorded. Next cooldown: {next_cooldown / 3600.0:.2f} hrs")

    def record_event_checked(self, event_id: str) -> None:
        """Permanently record an event ID as checked so it is never re-asked."""
        if not event_id:
            return
        checked = self._get_checked_event_ids()
        if event_id not in checked:
            checked.append(event_id)
            # Retain last 200 events to prevent boundless growth
            if len(checked) > 200:
                checked = checked[-200:]
            self._state_repo.set_value(KEY_CHECKED_EVENTS, checked)
            logger.info(f"[ IMPULSE ] Marked calendar event '{event_id}' as checked")

    def is_event_checked(self, event_id: str) -> bool:
        return event_id in self._get_checked_event_ids()

    def is_quiet_hours(self, dt: Optional[datetime] = None) -> bool:
        """Return True if within sleep hours (23:30 to 08:30)."""
        now = dt or datetime.now()
        current_hour_float = now.hour + (now.minute / 60.0)
        return current_hour_float >= QUIET_START_HOUR or current_hour_float < QUIET_END_HOUR

    def evaluate(
        self,
        active_events: Optional[List[Dict[str, Any]]] = None,
        recent_ended_events: Optional[List[Dict[str, Any]]] = None,
        has_curiosity_content: bool = True,
        now_dt: Optional[datetime] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Evaluate whether Chad should initiate an autonomous turn.

        Returns:
            (should_trigger, reason, context_data)
        """
        now = now_dt or datetime.now()
        now_epoch = time.time()

        # 1. Sleep hours rule
        if self.is_quiet_hours(now):
            return False, "quiet_sleep_hours", None

        # 2. Active calendar events
        if active_events:
            for ev in active_events:
                summary = (ev.get("summary") or "").lower()
                # If it's a chill event like class or studying, Matthew doesn't mind a text
                is_chill = any(c in summary for c in CHILL_EVENT_KEYWORDS)
                is_blocked = any(b in summary for b in BLOCKED_EVENT_KEYWORDS)

                if is_blocked and not is_chill:
                    return False, f"busy_event_{summary[:20]}", None

        # 3. Post-event checkin spike (e.g. gym, workout, class, church ended 15-45m ago)
        if recent_ended_events:
            for ev in recent_ended_events:
                eid = ev.get("id") or ""
                if eid and not self.is_event_checked(eid):
                    # Eligible for post-event checkin!
                    return True, "post_event_spike", ev

        # 4. Refractory cooldown check
        last_ts = self._get_last_spontaneous_ts()
        cooldown = self._get_cooldown_seconds()
        elapsed = now_epoch - last_ts

        if elapsed < cooldown:
            remaining_hrs = (cooldown - elapsed) / 3600.0
            return False, f"in_cooldown_{remaining_hrs:.1f}h_left", None

        # 5. Content check
        if not has_curiosity_content:
            return False, "no_curiosity_content", None

        # 6. Spontaneous impulse triggered!
        return True, "spontaneous_impulse", None
