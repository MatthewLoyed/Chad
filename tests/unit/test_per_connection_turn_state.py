"""Connection identity is per-connection and per-turn, never coordinator-wide (issue #711).

The Brain used to keep ONE `_client_type` for the whole process, set by whichever client
authenticated most recently. That value gates Brain-side turn persistence (#189): the
desktop Limb persists its own turns, so the Brain only writes when the client is a
browser/PWA. The desktop reconnects periodically and therefore routinely registered LAST:

    10:29:11  registered (client_type=browser)   <- phone
    10:29:17  registered (client_type=desktop)   <- desktop, seconds later

so a turn driven from the phone read "desktop", the Brain skipped persisting it, and the
desktop hadn't persisted it either (the turn wasn't its own). The turn rendered live and
was gone on reload. These tests lock the two halves of the fix: identity lives on the
CONNECTION, and the persistence decision is captured on the TURN when it starts.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.remote.limb_coordinator import ConnectedClient, LimbCoordinator
from src.remote.ui_state_syncer import UIStateSyncer
from src.shared.turn_delivery import DELIVERY_GUI


# ---------------------------------------------------------------------------
# A. Identity lives on the connection
# ---------------------------------------------------------------------------


def _coordinator():
    """LimbCoordinator with only the connection-registry surface wired up."""
    c = LimbCoordinator.__new__(LimbCoordinator)
    c._active_websocket = None
    c._clients = {}
    c.turn_coordinator = None
    for name in ("register_active_websocket", "client_type_for", "_maybe_preload_on_connect"):
        setattr(c, name, getattr(LimbCoordinator, name).__get__(c))
    # Preload-on-connect is a live-model side effect, not what's under test here.
    c._maybe_preload_on_connect = lambda: None
    return c


def test_a_later_desktop_connect_does_not_relabel_the_phone():
    """The exact repro: phone connects, desktop reconnects seconds later."""
    phone, desktop = object(), object()
    c = _coordinator()

    c.register_active_websocket(phone, client_type="browser")
    c.register_active_websocket(desktop, client_type="desktop")

    assert c.client_type_for(phone) == "browser", (
        "the phone is still a browser client after the desktop reconnects"
    )
    assert c.client_type_for(desktop) == "desktop"


def test_unknown_socket_defaults_to_desktop():
    """Fail safe: an unrecognised socket must not be treated as a browser and get
    its turns written twice (once by the Brain, once by the Limb that owns it)."""
    c = _coordinator()
    assert c.client_type_for(object()) == "desktop"


# ---------------------------------------------------------------------------
# B. The persistence decision is captured on the turn
# ---------------------------------------------------------------------------


class _FakeHistoryRepo:
    def __init__(self):
        self.appended = []
        self.titles = []

    async def append_message(self, conversation_id, role, content, metadata=None, message_id=None):
        self.appended.append((conversation_id, role, content))
        return {}

    async def get_conversation(self, conversation_id):
        return {"id": conversation_id, "title": "New Chat"}

    async def update_conversation_title(self, conversation_id, title):
        self.titles.append((conversation_id, title))
        return True

    @staticmethod
    def derive_title(text):
        return text[:20]


def _syncer(has_viewers=True):
    s = UIStateSyncer.__new__(UIStateSyncer)
    s._conversation_id_by_bucket = {}
    s._initiator_by_bucket = {}
    s._persist_by_bucket = {}
    s._completed_by_bucket = {}
    s._flush_thinking = AsyncMock()
    s._pending_thinking_broadcast = None

    sent = []

    async def _send_to_all(msg):
        sent.append(json.loads(msg.to_json()))

    s._limb = MagicMock(
        turn_coordinator=MagicMock(last_turn_metrics={}),
        has_viewers=has_viewers,
        history_repo=_FakeHistoryRepo(),
    )
    s._limb.send_to_active_limb = _send_to_all
    return s, sent


def _complete_event(text="the answer"):
    return SimpleNamespace(
        response=text,
        client_turn_id="turn-1",
        pending_approval=None,
        awaiting_approval=False,
        awaiting_input=False,
    )


def test_phone_turn_persists_even_though_the_desktop_connected_last():
    """The bug, end to end at the syncer: the turn was started by the phone, so it is
    persisted — no matter what kind of client happens to be 'current' when it ends."""
    s, _sent = _syncer()
    bucket = ("session-phone", DELIVERY_GUI)
    s._conversation_id_by_bucket[bucket] = "conv-phone"
    s._initiator_by_bucket[bucket] = "user"
    s.pin_turn_client(
        bucket, "browser", conversation_id="conv-phone",
        user_content="what's the weather", user_text="what's the weather",
    )

    # The desktop reconnecting mid-turn is exactly what used to flip this to "desktop".
    s._limb.client_type = "desktop"

    asyncio.run(s._broadcast_turn_complete_event(_complete_event(), initiator="user", bucket=bucket))

    roles = [(conv, role) for conv, role, _ in s._limb.history_repo.appended]
    assert roles == [("conv-phone", "user"), ("conv-phone", "assistant")], (
        "the phone's turn must be written, user message first, to ITS conversation"
    )


def test_desktop_turn_is_not_persisted_by_the_brain():
    """The desktop Limb writes its own turns — the Brain must not write them again."""
    s, _sent = _syncer()
    bucket = ("session-desk", DELIVERY_GUI)
    s._conversation_id_by_bucket[bucket] = "conv-desk"
    s._initiator_by_bucket[bucket] = "user"
    s.pin_turn_client(bucket, "desktop", conversation_id="conv-desk", user_content="hi", user_text="hi")

    asyncio.run(s._broadcast_turn_complete_event(_complete_event(), initiator="user", bucket=bucket))

    assert s._limb.history_repo.appended == []


def test_concurrent_turns_do_not_write_each_others_messages():
    """Two turns in flight at once: the pending user content used to be a single
    coordinator field, so the second turn's message overwrote the first's."""
    s, _sent = _syncer()
    phone_bucket = ("session-phone", DELIVERY_GUI)
    desk_bucket = ("session-desk", DELIVERY_GUI)
    for bucket, conv in ((phone_bucket, "conv-phone"), (desk_bucket, "conv-desk")):
        s._conversation_id_by_bucket[bucket] = conv
        s._initiator_by_bucket[bucket] = "user"

    s.pin_turn_client(phone_bucket, "browser", conversation_id="conv-phone",
                      user_content="phone question", user_text="phone question")
    # A second browser turn starts before the first finishes.
    s.pin_turn_client(desk_bucket, "browser", conversation_id="conv-desk",
                      user_content="other question", user_text="other question")

    asyncio.run(s._broadcast_turn_complete_event(
        _complete_event("phone answer"), initiator="user", bucket=phone_bucket
    ))

    assert s._limb.history_repo.appended[0] == ("conv-phone", "user", "phone question"), (
        "the first turn must persist its OWN user message, not the newer turn's"
    )


def test_turn_persists_with_no_viewer_attached():
    """iOS drops the socket whenever the PWA is backgrounded. The turn keeps running
    (see LimbCoordinator.unregister_websocket) — but the completion handler used to
    bail out early when no socket was attached, skipping the write it exists to do."""
    s, sent = _syncer(has_viewers=False)
    bucket = ("session-phone", DELIVERY_GUI)
    s._conversation_id_by_bucket[bucket] = "conv-phone"
    s._initiator_by_bucket[bucket] = "user"
    s.pin_turn_client(bucket, "browser", conversation_id="conv-phone",
                      user_content="q", user_text="q")

    asyncio.run(s._broadcast_turn_complete_event(_complete_event(), initiator="user", bucket=bucket))

    assert [r for _, r, _ in s._limb.history_repo.appended] == ["user", "assistant"], (
        "a turn nobody is watching still has to be saved"
    )


def test_a_completed_turn_is_never_persisted_twice():
    """Both completion events (TurnCompleteEvent and TurnCompleted) can fire for one
    turn; the bucket's record is consumed on the first write."""
    s, _sent = _syncer()
    bucket = ("session-phone", DELIVERY_GUI)
    s._conversation_id_by_bucket[bucket] = "conv-phone"
    s._initiator_by_bucket[bucket] = "user"
    s.pin_turn_client(bucket, "browser", conversation_id="conv-phone",
                      user_content="q", user_text="q")

    async def twice():
        await s._persist_browser_turn(bucket, "answer", "turn-1", {}, initiator="user")
        await s._persist_browser_turn(bucket, "answer", "turn-1", {}, initiator="user")

    asyncio.run(twice())

    assert len(s._limb.history_repo.appended) == 2, "one user + one assistant row, not four"


# ---------------------------------------------------------------------------
# C. The desktop Limb only writes turns it started
# ---------------------------------------------------------------------------


def _desktop_limb(turn_is_mine):
    """A desktop Limb with one chat open and a completed turn arriving over the socket."""
    from src.remote.gui_limb import GUIChadLimb

    limb = GUIChadLimb(MagicMock(), server_url="ws://localhost:8767")
    limb.pending_conversation_id = "conv-desktop"
    limb.history_repo = _FakeHistoryRepo()
    limb._pending_user_content = "typed on the desktop"
    limb._pending_user_text = "typed on the desktop"
    limb._turn_is_mine = turn_is_mine
    return limb


def _deliver_turn(limb):
    from src.remote.protocol import ChadMessage, MessageType

    msg = ChadMessage(
        MessageType.AI_RESPONSE,
        payload={"text": "the answer", "initiator": "user", "conversation_id": "conv-desktop"},
    )
    asyncio.run(limb.handle_message(msg))


def test_desktop_limb_persists_the_turn_it_started():
    limb = _desktop_limb(turn_is_mine=True)

    _deliver_turn(limb)

    roles = [role for _, role, _ in limb.history_repo.appended]
    assert roles == ["user", "assistant"], "a turn typed here is still saved here"


def test_desktop_limb_does_not_persist_a_turn_it_only_watched():
    """Post-#709 the Brain fans every turn out to every viewer, so a phone-driven turn
    also lands here. `pending_conversation_id` is the chat THIS window has open, and the
    Brain already persists browser turns (#189) — writing again duplicated the user
    message and filed the phone's reply under the desktop's conversation."""
    limb = _desktop_limb(turn_is_mine=False)

    _deliver_turn(limb)

    assert limb.history_repo.appended == [], "a turn we merely watched must not be written"
    limb.window.push_ai_response.assert_called_once()
    _args, kwargs = limb.window.push_ai_response.call_args
    assert "thoughts" in kwargs.get("metadata", {}), (
        "still rendered normally — only the DB write is suppressed"
    )


def test_event_status_without_running_loop_does_not_raise():
    """EventBus publishes events synchronously across threads. When no loop is running,
    _on_event_status must not raise RuntimeError: no running event loop."""
    limb = MagicMock()
    limb.has_viewers = False
    syncer = UIStateSyncer(limb, event_bus=MagicMock())

    event = SimpleNamespace(status="Formulating...", delivery="gui", client_turn_id="t1")
    # Calling in this thread with no active event loop running
    syncer._on_event_status(event)

