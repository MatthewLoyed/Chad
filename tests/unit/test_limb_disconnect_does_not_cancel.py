"""A dropped socket must not abandon the turn running on this machine.

The turn executes entirely on the Brain; the WebSocket is only the viewport onto it.
`unregister_websocket` used to cancel every in-flight turn on any disconnect, which broke
two real cases found from phone use of the PWA:

  * iOS drops the socket whenever the app is backgrounded. Cancelling there killed the turn
    mid-flight — and browser turns are persisted as a unit when they COMPLETE (#189), so a
    cancelled turn wrote nothing at all. Coming back showed only whatever had finished
    before looking away, which reads as "my session got wiped".
  * The cancel ran outside the is-this-the-active-socket check, so a stale socket's late
    close event cancelled the turn belonging to the connection that had already replaced
    it — the exact ordering a reconnect produces.
"""

import asyncio
import pytest


class _Messages:
    def __init__(self):
        self.cancel_calls = 0

    async def cancel_active_tasks(self):
        self.cancel_calls += 1


class _Coordinator:
    """Minimal stand-in exposing only what unregister_websocket touches."""

    def __init__(self, client_type: str, active):
        from src.remote.limb_coordinator import ConnectedClient, LimbCoordinator

        self._active_websocket = active
        self._clients = (
            {active: ConnectedClient(websocket=active, client_type=client_type)}
            if active is not None else {}
        )
        self.messages = _Messages()
        self.unregister_websocket = LimbCoordinator.unregister_websocket.__get__(self)
        self.client_type_for = LimbCoordinator.client_type_for.__get__(self)


def test_browser_disconnect_lets_the_turn_finish():
    """Backgrounding a phone must not cancel the turn — it has to finish so it persists."""
    ws = object()
    c = _Coordinator("browser", ws)

    asyncio.run(c.unregister_websocket(ws))

    assert c.messages.cancel_calls == 0, "a phone going to the background must not kill the turn"
    assert c._active_websocket is None, "the socket itself is still unregistered"


def test_desktop_disconnect_still_cancels():
    """Closing the desktop window is a deliberate 'I'm done', unlike backgrounding a phone."""
    ws = object()
    c = _Coordinator("desktop", ws)

    asyncio.run(c.unregister_websocket(ws))

    assert c.messages.cancel_calls == 1
    assert c._active_websocket is None


@pytest.mark.parametrize("client_type", ["desktop", "browser"])
def test_stale_socket_close_never_cancels_the_live_turn(client_type):
    """A reconnect registers the new socket first, then the old one's close arrives. That
    close must not cancel work belonging to the connection that replaced it."""
    live_ws = object()
    stale_ws = object()
    c = _Coordinator(client_type, live_ws)

    asyncio.run(c.unregister_websocket(stale_ws))

    assert c.messages.cancel_calls == 0
    assert c._active_websocket is live_ws, "the live socket must stay registered"
    assert c.client_type_for(live_ws) == client_type, (
        "a stale close must not reset the live connection's client type"
    )


# ---------------------------------------------------------------------------
# Multiple viewers
#
# `_active_websocket` is a single slot, so with the desktop GUI and a phone both
# connected, whichever registered LAST won and the other went silent — a turn started
# on the phone streamed to the desktop, because the desktop happened to reconnect
# after the phone. No ordering makes one slot correct for two real viewers.
# ---------------------------------------------------------------------------


class _FakeWS:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.sent = []

    async def send(self, payload):
        if self.fail:
            raise RuntimeError(f"{self.name} socket is dead")
        self.sent.append(payload)


class _Sender:
    """Stand-in exposing only what send_to_active_limb touches."""

    def __init__(self, connected, active=None):
        from src.remote.limb_coordinator import ConnectedClient, LimbCoordinator

        self._clients = {ws: ConnectedClient(websocket=ws) for ws in connected}
        self._active_websocket = active
        self.send_to_active_limb = LimbCoordinator.send_to_active_limb.__get__(self)
        self._fan_out = LimbCoordinator._fan_out.__get__(self)


class _Msg:
    def to_json(self):
        return '{"type":"ai_response"}'


def test_turn_output_reaches_every_connected_viewer():
    phone, desktop = _FakeWS("phone"), _FakeWS("desktop")
    s = _Sender([phone, desktop], active=desktop)

    asyncio.run(s.send_to_active_limb(_Msg()))

    assert len(phone.sent) == 1, "the phone must receive the turn it started"
    assert len(desktop.sent) == 1, "the desktop still sees it too"


def test_one_dead_viewer_does_not_starve_the_others():
    phone, desktop = _FakeWS("phone", fail=True), _FakeWS("desktop")
    s = _Sender([phone, desktop], active=desktop)

    asyncio.run(s.send_to_active_limb(_Msg()))

    assert len(desktop.sent) == 1, "a dead phone must not stop the desktop mid-turn"
    assert phone not in s._clients, "the dead viewer is dropped"


def test_falls_back_to_the_active_socket_when_the_viewer_set_is_empty():
    """Defensive: a caller that registered before this set existed still gets its output."""
    only = _FakeWS("legacy")
    s = _Sender([], active=only)

    asyncio.run(s.send_to_active_limb(_Msg()))

    assert len(only.sent) == 1
