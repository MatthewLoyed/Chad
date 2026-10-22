"""Concurrent-Limbs state-isolation coverage for issue #216
("two Limbs connected simultaneously, no state bleed").

test_coordinator_approval_isolation.py proves a SINGLE bucket stamps its own
conversation_id correctly. This extends that to the actual multi-session
scenario: two independent (session_id, delivery_channel) buckets registered
on the SAME UIStateSyncer instance (as happens when two Limbs/tabs are
connected at once) must never cross-contaminate each other's conversation_id,
initiator, or approval payload -- each bucket's broadcast must only ever see
its own state.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import src.remote.ui_state_syncer as ui_state_syncer
from src.remote.ui_state_syncer import UIStateSyncer
from src.remote.protocol import MessageType
from src.shared.turn_delivery import DELIVERY_GUI


def _two_session_syncer(monkeypatch):
    syncer = UIStateSyncer.__new__(UIStateSyncer)

    ws_a = AsyncMock()
    ws_b = AsyncMock()
    bucket_a = ("session-A", DELIVERY_GUI)
    bucket_b = ("session-B", DELIVERY_GUI)

    syncer._conversation_id_by_bucket = {bucket_a: "conv-A", bucket_b: "conv-B"}
    syncer._initiator_by_bucket = {bucket_a: "user", bucket_b: "user"}
    syncer._persist_by_bucket = {}
    syncer._is_turn_complete = MagicMock(return_value=False)
    syncer._mark_turn_complete = MagicMock()
    syncer._flush_thinking = AsyncMock()

    # Both viewers receive every frame (issue #711 fan-out). Isolation can therefore
    # no longer come from "which socket did this go to" — it has to come from the
    # payload each bucket stamps, which is the stronger property anyway.
    async def _send_to_all(msg):
        for ws in (ws_a, ws_b):
            await ws.send(msg.to_json())

    syncer._limb = MagicMock(
        turn_coordinator=MagicMock(last_turn_metrics={}),
        has_viewers=True,
    )
    syncer._limb.send_to_active_limb = _send_to_all

    push = MagicMock()
    push.send = AsyncMock()
    monkeypatch.setattr(ui_state_syncer, "get_push_service", lambda: push, raising=False)
    monkeypatch.setattr(
        "src.remote.push_service.get_push_service", lambda: push, raising=False
    )

    return syncer, bucket_a, bucket_b, ws_a, ws_b


def _event(response_text: str, token: str) -> SimpleNamespace:
    return SimpleNamespace(
        response=response_text,
        client_turn_id="turn-1",
        pending_approval={"token": token, "tool_name": "ask_user_question"},
        awaiting_approval=False,
        awaiting_input=False,
    )


def test_two_concurrent_sessions_each_get_their_own_conversation_id(monkeypatch):
    syncer, bucket_a, bucket_b, ws_a, ws_b = _two_session_syncer(monkeypatch)

    async def run_both():
        # Simulate near-simultaneous turns landing on two different sessions,
        # interleaved (not sequential) to actually exercise shared-state races.
        await asyncio.gather(
            syncer._broadcast_turn_complete_event(
                _event("answer for A", "tok-A"), initiator="user", bucket=bucket_a
            ),
            syncer._broadcast_turn_complete_event(
                _event("answer for B", "tok-B"), initiator="user", bucket=bucket_b
            ),
        )

    asyncio.run(run_both())

    for ws, label in ((ws_a, "viewer A"), (ws_b, "viewer B")):
        sent = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        approvals = {
            m["payload"]["token"]: m["payload"]
            for m in sent
            if m["type"] == MessageType.TOOL_APPROVAL_REQUEST.value
        }
        # Each turn's card carries ITS OWN conversation_id, regardless of which viewer
        # it reaches or which turn was mid-flight alongside it -- no cross-bucket bleed.
        assert approvals["tok-A"]["conversation_id"] == "conv-A", label
        assert approvals["tok-B"]["conversation_id"] == "conv-B", label

    # The bucket-keyed dicts themselves must remain unmutated/uncrossed after both turns.
    assert syncer._conversation_id_by_bucket[bucket_a] == "conv-A"
    assert syncer._conversation_id_by_bucket[bucket_b] == "conv-B"
