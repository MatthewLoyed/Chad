"""
Calendar Schedule Client — Google Calendar schedule awareness for knowledge injection.

Fifth knowledge source (alongside GraphRAG, ActivityRAG, DriveCatalog, and SessionHistory).
Surfaces upcoming calendar events so Chad can proactively comment on upcoming classes/events.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = setup_logger = logging.getLogger("CalendarScheduleClient")


class CalendarScheduleClient:
    """Async client for querying upcoming Google Calendar schedule context."""

    def __init__(self, lookahead_hours: int = 4):
        self._lookahead_hours = lookahead_hours

    async def search(
        self,
        query: str = "",
        limit: Optional[int] = None,
        domain: Optional[str] = None
    ) -> Optional[str]:
        """
        Fetch upcoming schedule context from Google Calendar.
        Returns a formatted schedule block or None if no upcoming events.
        """
        try:
            from src.shared.config_manager import ConfigManager
            if not ConfigManager.get("calendar.proactive_awareness", True):
                return None

            from src.tools.workspace.calendar import get_upcoming_schedule_context

            hours = self._lookahead_hours
            if limit is not None and limit > 0:
                max_events = limit
            else:
                max_events = 5

            # Run off-thread to avoid blocking async event loop
            context = await asyncio.to_thread(get_upcoming_schedule_context, hours, max_events)
            return context if context and context.strip() else None

        except Exception as e:
            logger.debug(f"[ CALENDAR ] Schedule context query error: {e}")
            return None
