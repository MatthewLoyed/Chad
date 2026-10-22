"""Unit tests for WebSocket message isolation and queue draining in eval harness."""
from __future__ import annotations

import asyncio
import json
import pytest

from evals.live import LiveTrace, _collect, drain_ws


class FakeWebSocket:
    def __init__(self, messages: list[str]):
        self.queue = asyncio.Queue()
        for m in messages:
            self.queue.put_nowait(m)

    async def recv(self) -> str:
        return await self.queue.get()


@pytest.mark.asyncio
async def test_collect_filters_cross_conversation_messages():
    """Verify that lingering messages from a previous/different conversation are dropped."""
    stale_ai_response = json.dumps({
        "type": "ai_response",
        "payload": {
            "conversation_id": "conv_stale_123",
            "text": "Stale response from previous case",
            "awaiting_approval": True,
            "fragment": False,
        },
    })
    correct_ai_response = json.dumps({
        "type": "ai_response",
        "payload": {
            "conversation_id": "conv_active_456",
            "text": "Correct response for current case",
            "stop_reason": "task_completed",
            "fragment": False,
        },
    })

    ws = FakeWebSocket([stale_ai_response, correct_ai_response])
    trace = LiveTrace(prompt="test", category="planning")
    reasoning: list[str] = []
    results: list[str] = []

    await _collect(ws, trace, reasoning, results, expected_conv="conv_active_456")

    assert trace.final_response == "Correct response for current case"
    assert trace.stop_reason == "task_completed"


@pytest.mark.asyncio
async def test_drain_ws_clears_stale_buffer():
    """Verify that drain_ws consumes all pending messages in the socket buffer."""
    ws = FakeWebSocket(["msg1", "msg2", "msg3"])
    drained = await drain_ws(ws, timeout=0.01)
    assert drained == 3
    assert ws.queue.empty()
