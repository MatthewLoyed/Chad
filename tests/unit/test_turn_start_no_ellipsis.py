"""Turn-start must not seed the thinking stream with a literal '...' placeholder."""

from src.remote.ui_state_syncer import UIStateSyncer


def test_turn_start_does_not_broadcast_ellipsis_placeholder(monkeypatch):
    syncer = UIStateSyncer.__new__(UIStateSyncer)
    syncer._initiator_by_bucket = {}
    syncer._completed_by_bucket = {}
    syncer._broadcast_calls = []

    def _fake_broadcast(*args, **kwargs):
        syncer._broadcast_calls.append((args, kwargs))

    monkeypatch.setattr(syncer, "_broadcast_thinking_update", _fake_broadcast)
    monkeypatch.setattr(syncer, "_bucket_from_event", lambda e: ("sess", "gui"))
    monkeypatch.setattr(syncer, "_is_chat_turn", lambda bucket: True)

    class _Evt:
        initiator = "user"

    syncer._on_event_turn_start(_Evt())

    assert syncer._initiator_by_bucket[("sess", "gui")] == "user"
    assert syncer._completed_by_bucket[("sess", "gui")] is False
    assert syncer._broadcast_calls == [], (
        "turn_start must not push '...' into the thinking stream "
        f"(got {syncer._broadcast_calls!r})"
    )
