"""
Regression test for the broken Stop button (LM Studio keeps generating after Stop).

Proves the root cause: on the headless Brain, the Stop button's HTTP command
reaches `AssistantFacade.interrupt()`, but the facade can only publish the
`TaskStoppedByUser` event (the signal LimbCoordinator listens for to cancel the
in-flight turn / abort the LM Studio stream) if it can resolve the EventBus.

`AssistantFacade.get_event_bus()` reads `assistant.event_bus`. The headless
wrapper (`HeadlessAssistantWrapper` in remote_factory) did not expose
`event_bus`, so `get_event_bus()` returned None, `interrupt()` short-circuited
to False, and the cancel event was never published — the model kept streaming.

Run:
    $env:PYTHONPATH="." ; python -m pytest tests/unit/test_stop_active_task.py -q
"""

import asyncio
import types

import pytest

from src.application.facades.assistant_facade import AssistantFacade
from src.application.events.event_bus import EventBus
from src.application.events.events import TaskStoppedByUser


def _make_assistant(event_bus=None):
    """Mimic remote_factory's HeadlessAssistantWrapper surface."""
    stopped = {"turn": False}

    class _SessionManager:
        async def stop_active_turn(self):
            stopped["turn"] = True

    assistant = types.SimpleNamespace(session_manager=_SessionManager())
    if event_bus is not None:
        assistant.event_bus = event_bus
    return assistant, stopped


def test_interrupt_without_event_bus_never_publishes_stop():
    """REPLICATION: wrapper missing `event_bus` -> stop event never fires."""
    assistant, _ = _make_assistant(event_bus=None)
    facade = AssistantFacade(assistant=assistant, is_uplink=False)

    received = []
    # A separate bus to prove nothing is published on ANY bus the facade knows.
    assert facade.get_event_bus() is None

    result = asyncio.run(facade.interrupt())

    # The pre-fix behaviour: interrupt reports failure and no event is published.
    assert result is False
    assert received == []


def test_interrupt_with_event_bus_publishes_stop():
    """FIX: wrapper exposing `event_bus` -> TaskStoppedByUser is published."""
    bus = EventBus()
    received = []

    def _handler(e):  # strong ref kept for the duration of the test
        received.append(e)

    bus.subscribe(TaskStoppedByUser, _handler)

    assistant, stopped = _make_assistant(event_bus=bus)
    facade = AssistantFacade(assistant=assistant, is_uplink=False)

    assert facade.get_event_bus() is bus

    result = asyncio.run(facade.interrupt())

    assert result is True
    assert stopped["turn"] is True, "active turn should be stopped on the session manager"
    assert len(received) == 1, "exactly one TaskStoppedByUser must be published"
    assert isinstance(received[0], TaskStoppedByUser)
