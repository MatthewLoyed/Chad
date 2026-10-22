"""Unit: UI tool_end must not drop short-summary sibling list payloads."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.remote.ui_state_syncer import UIStateSyncer


@pytest.mark.asyncio
async def test_broadcast_tool_end_appends_sibling_emails_when_result_is_summary():
    limb = MagicMock(has_viewers=True)
    ws = MagicMock()
    ws.send = AsyncMock()

    # The syncer hands frames to the coordinator's fan-out rather than writing to a
    # single active socket, so every viewer sees the turn (issue #711).
    async def _send_to_all(msg):
        await ws.send(msg.to_json())

    limb.send_to_active_limb = _send_to_all
    syncer = UIStateSyncer(limb, event_bus=MagicMock())

    await syncer._broadcast_tool_end(
        "search_gmail",
        {
            "success": True,
            "result": "Found 1 emails. Call read_gmail.",
            "emails": [{"id": "m1", "subject": "Hello", "from_name": "Sam"}],
        },
        success=True,
    )

    assert ws.send.await_count == 1
    raw = ws.send.await_args.args[0]
    # ChadMessage.to_json() string — must include subject evidence
    assert "Hello" in raw
    assert "emails" in raw
