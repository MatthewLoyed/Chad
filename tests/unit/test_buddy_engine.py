"""Tests for desktop buddy state resolution and cue rules."""

import time

from src.application.events import (
    ErrorOccurred,
    ToolCompleted,
    ToolStarted,
    TurnCompleted,
    TurnStarted,
)
from src.application.events.event_bus import EventBus
from src.buddy import BuddyEngine, BuddyRule, BuddyRegistry
from src.remote.buddy_bridge import BuddyBridge


def test_buddy_idle_by_default():
    bus = EventBus()
    engine = BuddyEngine(bus)
    snap = engine.get_snapshot()
    assert snap.mode == "idle"
    assert snap.cue is None


def test_buddy_thinking_on_turn():
    bus = EventBus()
    engine = BuddyEngine(bus)
    bus.publish(TurnStarted(user_input="hi"))
    assert engine.get_snapshot().mode == "thinking"


def test_buddy_working_while_tools_active():
    bus = EventBus()
    engine = BuddyEngine(bus)
    bus.publish(TurnStarted(user_input="hi"))
    bus.publish(ToolStarted(tool_name="test", args={}))
    assert engine.get_snapshot().mode == "working"
    bus.publish(ToolCompleted(tool_name="test", result="ok", success=True))
    assert engine.get_snapshot().mode == "thinking"


def test_buddy_idle_after_turn():
    bus = EventBus()
    engine = BuddyEngine(bus)
    bus.publish(TurnStarted(user_input="hi"))
    bus.publish(TurnCompleted())
    assert engine.get_snapshot().mode == "idle"


def test_buddy_error_on_failure():
    bus = EventBus()
    engine = BuddyEngine(bus)
    bus.publish(ErrorOccurred(error_message="boom", error_type="test"))
    assert engine.get_snapshot().mode == "error"


def test_buddy_success_cue_on_tool_complete():
    bus = EventBus()
    engine = BuddyEngine(bus)
    bus.publish(ToolCompleted(tool_name="any_tool", result="ok", success=True))
    snap = engine.get_snapshot()
    assert snap.cue is not None
    assert snap.cue.animation == "success"
    assert snap.cue.rule_name == "tool_success"


def test_buddy_custom_rule_for_specific_tool():
    bus = EventBus()
    engine = BuddyEngine(bus)
    engine.register_rule(
        BuddyRule(
            name="email_sent",
            events=(ToolCompleted,),
            match=lambda e: e.success and e.tool_name == "send_email",
            cue="email_sent",
            cue_duration_s=2.0,
            meta_from_event=lambda e: {"tool": e.tool_name},
            priority=50,
        )
    )
    bus.publish(ToolCompleted(tool_name="send_email", result="sent", success=True))
    snap = engine.get_snapshot()
    assert snap.cue is not None
    assert snap.cue.animation == "email_sent"
    assert snap.cue.meta == {"tool": "send_email"}


def test_buddy_bridge_alias():
    bus = EventBus()
    bridge = BuddyBridge(bus)
    assert bridge.get_state()["state"] == "idle"


def test_buddy_state_dict_includes_mode_and_legacy_state():
    bus = EventBus()
    engine = BuddyEngine(bus)
    bus.publish(TurnStarted(user_input="hi"))
    payload = engine.get_state()
    assert payload["state"] == "thinking"
    assert payload["mode"] == "thinking"


def test_buddy_notifies_subscribers():
    bus = EventBus()
    engine = BuddyEngine(bus)
    seen: list[dict] = []
    engine.subscribe(seen.append)
    bus.publish(TurnStarted(user_input="hi"))
    assert seen[-1]["state"] == "thinking"


def test_buddy_listening_on_wake_word():
    bus = EventBus()
    engine = BuddyEngine(bus)
    seen: list[dict] = []
    engine.subscribe(seen.append)

    engine.set_listening(True)
    assert engine.get_snapshot().mode == "listening"
    assert seen[-1]["state"] == "listening"

    # Cleared when the turn begins; normal modes take over.
    engine.set_listening(False)
    assert engine.get_snapshot().mode == "idle"


def test_buddy_error_outranks_listening():
    bus = EventBus()
    engine = BuddyEngine(bus)
    engine.set_listening(True)
    bus.publish(ErrorOccurred(error_message="boom", error_type="test"))
    assert engine.get_snapshot().mode == "error"


def test_buddy_error_mode_expires():
    bus = EventBus()
    quick_error = BuddyRule(
        name="quick_error",
        events=(ErrorOccurred,),
        mode="error",
        cue="error",
        cue_duration_s=0.05,
        priority=100,
    )
    engine = BuddyEngine(bus, registry=BuddyRegistry((quick_error,)))
    bus.publish(ErrorOccurred(error_message="x", error_type="test"))
    assert engine.get_snapshot().mode == "error"
    time.sleep(0.08)
    assert engine.get_snapshot().mode == "idle"
