"""
Dashboard feed commands.

Relay the Dashboard workspace's reads/writes to the Brain. These commands call
the standalone async helpers in ``src.core.intelligence.dashboard_feed``
directly (the data is a simple Supabase table, not facade state), mirroring
how the capability-suggestions panel is dispatched through ``/api/command``.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from .base_command import Command


@dataclass
class GetDashboardFeedCommand(Command):
    """Fetch the dashboard feed (pinned life widgets + reverse-chron entries)."""

    limit: int = 50

    async def execute(self, facade) -> List[Dict[str, Any]]:
        from src.core.intelligence.dashboard_feed import get_feed
        return await get_feed(limit=int(self.limit))

    def __str__(self) -> str:
        return f"GetDashboardFeedCommand(limit={self.limit})"


@dataclass
class RunDashboardActionCommand(Command):
    """Execute the single runnable step attached to an 'action' entry."""

    entry_id: str

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.entry_id:
            return False, "entry_id is required"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.core.intelligence.dashboard_feed import run_entry_action
        tool_registry = getattr(getattr(facade._assistant, 'session_manager', None), 'tools', None)
        return await run_entry_action(self.entry_id, tool_registry)

    def __str__(self) -> str:
        return f"RunDashboardActionCommand(id={self.entry_id})"


@dataclass
class DismissDashboardEntryCommand(Command):
    """Dismiss a dashboard entry (drops it from the feed)."""

    entry_id: str

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.entry_id:
            return False, "entry_id is required"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.core.intelligence.dashboard_feed import set_entry_status
        ok = await set_entry_status(self.entry_id, "dismissed")
        return {"success": ok}

    def __str__(self) -> str:
        return f"DismissDashboardEntryCommand(id={self.entry_id})"
