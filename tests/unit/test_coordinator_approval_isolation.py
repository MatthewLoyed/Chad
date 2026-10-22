"""
Server-side session-isolation invariant (issue #269): the UIStateSyncer MUST
stamp the bucket's conversation_id onto the TOOL_APPROVAL_REQUEST it broadcasts.

This is the keystone of the whole isolation contract — if the approval card ships
without a conversation_id, every downstream guard (desktop Limb, browser transport,
React store) falls back to its legacy "untagged → allow" path and the card can leak
onto whatever chat the user is viewing. Locking the stamp here keeps that from
silently regressing.

Built via __new__ + stubs so no live Brain/websocket is required.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import src.remote.ui_state_syncer as ui_state_syncer
from src.remote.ui_state_syncer import UIStateSyncer
from src.remote.protocol import MessageType
from src.shared.turn_delivery import DELIVERY_GUI


def _fanout_limb(*sockets):
    """Stand-in Limb whose sends fan out to every viewer (issue #711).

    The syncer no longer writes to a single `active_websocket`; it hands frames to
    LimbCoordinator.send_to_active_limb, which delivers to all connected clients.
    """
    limb = MagicMock(
        turn_coordinator=MagicMock(last_turn_metrics={}),
        has_viewers=True,
    )

    async def _send_to_all(msg):
        for ws in sockets:
            await ws.send(msg.to_json())

    limb.send_to_active_limb = _send_to_all
    return limb


def _run_complete_event(conversation_id, monkeypatch):
    """Drive _broadcast_turn_complete_event with a pending approval; return sent msgs."""
    syncer = UIStateSyncer.__new__(UIStateSyncer)

    ws = AsyncMock()
    bucket = ("session-xyz", DELIVERY_GUI)
    syncer._limb = _fanout_limb(ws)
    syncer._conversation_id_by_bucket = {bucket: conversation_id}
    syncer._initiator_by_bucket = {bucket: "user"}
    syncer._persist_by_bucket = {}

    # Stub the collaborators the approval path calls but that aren't under test.
    syncer._is_turn_complete = MagicMock(return_value=False)
    syncer._mark_turn_complete = MagicMock()
    syncer._flush_thinking = AsyncMock()

    # Avoid real web-push side effects.
    push = MagicMock()
    push.send = AsyncMock()
    monkeypatch.setattr(ui_state_syncer, "get_push_service", lambda: push, raising=False)
    monkeypatch.setattr(
        "src.remote.push_service.get_push_service", lambda: push, raising=False
    )

    event = SimpleNamespace(
        response="Here is my answer",
        client_turn_id="turn-1",
        pending_approval={"token": "tok-1", "tool_name": "ask_user_question"},
        awaiting_approval=False,
        awaiting_input=False,
    )

    asyncio.run(syncer._broadcast_turn_complete_event(event, initiator="user", bucket=bucket))
    return [json.loads(call.args[0]) for call in ws.send.call_args_list]


def test_approval_message_carries_conversation_id(monkeypatch):
    sent = _run_complete_event("conv-a", monkeypatch)

    approvals = [m for m in sent if m["type"] == MessageType.TOOL_APPROVAL_REQUEST.value]
    assert len(approvals) == 1, "expected exactly one approval message"
    assert approvals[0]["payload"]["conversation_id"] == "conv-a"


def test_stamp_does_not_mutate_the_events_pending_approval(monkeypatch):
    # The syncer must copy the dict, never mutate the event's own payload.
    syncer = UIStateSyncer.__new__(UIStateSyncer)
    ws = AsyncMock()
    bucket = ("session-xyz", DELIVERY_GUI)
    syncer._limb = _fanout_limb(ws)
    syncer._conversation_id_by_bucket = {bucket: "conv-a"}
    syncer._initiator_by_bucket = {bucket: "user"}
    syncer._persist_by_bucket = {}
    syncer._is_turn_complete = MagicMock(return_value=False)
    syncer._mark_turn_complete = MagicMock()
    syncer._flush_thinking = AsyncMock()
    push = MagicMock(send=AsyncMock())
    monkeypatch.setattr(ui_state_syncer, "get_push_service", lambda: push, raising=False)
    monkeypatch.setattr("src.remote.push_service.get_push_service", lambda: push, raising=False)

    original = {"token": "tok-1", "tool_name": "ask_user_question"}
    event = SimpleNamespace(
        response="answer",
        client_turn_id="turn-1",
        pending_approval=original,
        awaiting_approval=False,
        awaiting_input=False,
    )

    asyncio.run(syncer._broadcast_turn_complete_event(event, initiator="user", bucket=bucket))

    assert "conversation_id" not in original, "event payload was mutated in place"
