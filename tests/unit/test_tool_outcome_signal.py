"""The tool-outcome signal must reflect what actually happened.

This had NO test, which is how it stayed wrong for a long time. Tools signal
failure by RETURNING {"success": False}; they do not raise. Treating "did not
raise" as success made every failed call log as a success, and three
self-improvement systems read that column:
  * OATS embedding refinement (tool selection)
  * capability_patterns (groups "successful" runs into suggested skills)
  * reflection (lesson generation)
All three were learning from data that said everything always worked.
"""

import pytest

from src.application.events import ToolCompleted
from src.core.tools import failure
from src.infrastructure.tools.event_wrapper import ToolEventWrapper


class _Bus:
    """Minimal stub — the wrapper only ever calls `.publish()`.

    Deliberately NOT an EventBus subclass: EventBus is a singleton, so
    `_Bus()` would hand back the shared instance once any other test has
    constructed one, and the capture list would silently vanish.
    """

    def __init__(self):
        self.completed = []

    def publish(self, event):
        if isinstance(event, ToolCompleted):
            self.completed.append(event)


async def _run(result, monkeypatch):
    """Execute the wrapper over a stub tool, capturing the OATS outcome."""
    logged = {}

    bus = _Bus()

    async def _executor(tool_name, kwargs):
        return result

    wrapper = ToolEventWrapper.wrap_as_langchain_tool(
        tool_name="stub_tool", original_executor=_executor, event_bus=bus
    )
    monkeypatch.setattr(
        ToolEventWrapper, "_get_tool_call_id", staticmethod(lambda *a, **k: "cid")
    )
    # OATS logging short-circuits with no turn context; we assert on the event,
    # which is driven by the same `succeeded` value.
    await wrapper()
    logged["events"] = bus.completed
    return logged


@pytest.mark.asyncio
async def test_failed_tool_is_not_reported_as_success(monkeypatch):
    envelope = failure.failure("401 Unauthorized", tool_name="stub_tool")
    out = await _run(envelope, monkeypatch)

    assert len(out["events"]) == 1
    event = out["events"][0]
    assert event.success is False, "a failed tool must not be logged as a success"
    assert event.error


@pytest.mark.asyncio
async def test_successful_tool_is_reported_as_success(monkeypatch):
    out = await _run({"success": True, "result": "fine"}, monkeypatch)
    event = out["events"][0]
    assert event.success is True
    assert event.error is None


@pytest.mark.asyncio
async def test_non_dict_result_counts_as_success(monkeypatch):
    """Plenty of tools return a bare string/list; absence of a success flag is
    not evidence of failure."""
    out = await _run("just a string", monkeypatch)
    assert out["events"][0].success is True


@pytest.mark.asyncio
async def test_pending_approval_is_not_a_failure(monkeypatch):
    """The approval gate short-circuits with success=True and _pending_approval;
    a pause must not be recorded as a failed tool call."""
    out = await _run(
        {"success": True, "_pending_approval": True, "approval_token": "T1"}, monkeypatch
    )
    event = out["events"][0]
    assert event.success is True
    assert event.awaiting_user is True
