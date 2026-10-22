"""Concurrent turns must not overwrite each other's per-turn state.

``TurnCoordinator`` is built once per Brain and shared by every session, so a
background daemon turn running alongside a user turn (or a phone and a desktop
client) used to race on ``last_turn_metrics`` / ``_current_turn_mode`` /
``_last_tool_selection`` — whichever turn finished last won, and ``UIStateSyncer``
then reported the wrong turn's token counts.

These tests pin the fix (per-turn state in a ContextVar). Before it, the
interleaved test below reported the *same* metrics object for both turns.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.orchestration.turn.scratch import (
    TurnScratch,
    begin_turn,
    current_scratch,
    turn_scratch_var,
)


@pytest.mark.asyncio
async def test_concurrent_tasks_get_isolated_scratch():
    """Two turns interleaved in separate tasks each keep their own state."""
    started = asyncio.Event()

    async def turn(name: str, hold: bool) -> tuple:
        scratch = begin_turn()
        scratch.mode = name
        scratch.metrics["who"] = name
        if hold:
            started.set()
            await asyncio.sleep(0.05)          # let the other turn run in between
        else:
            await started.wait()
            await asyncio.sleep(0)
        # after yielding to the other turn, our own state must be untouched
        return current_scratch().mode, current_scratch().metrics["who"]

    first, second = await asyncio.gather(turn("alpha", True), turn("beta", False))
    assert first == ("alpha", "alpha")
    assert second == ("beta", "beta")


@pytest.mark.asyncio
async def test_scratch_visible_to_awaiting_caller():
    """An awaited coroutine shares the caller's context, so results survive the
    return — this is how evals/inprocess.py and text_mode read last_turn_metrics."""
    turn_scratch_var.set(None)

    async def run_turn():
        scratch = begin_turn()
        scratch.metrics["stop_reason"] = "task_completed"

    await run_turn()
    assert current_scratch() is not None
    assert current_scratch().metrics["stop_reason"] == "task_completed"


@pytest.mark.asyncio
async def test_event_handler_task_sees_the_publishing_turn_state():
    """EventBus dispatches async handlers via create_task, which copies the context.
    A handler spawned mid-turn must observe that turn's scratch, including mutations
    made after the task was created (metrics is mutated in place, never reassigned)."""
    seen = {}

    async def handler():
        seen["who"] = current_scratch().metrics.get("who")
        seen["late"] = current_scratch().metrics.get("late")

    async def turn():
        scratch = begin_turn()
        scratch.metrics["who"] = "turn-1"
        task = asyncio.create_task(handler())   # context copied here
        scratch.metrics["late"] = "added-after"  # in-place, same dict object
        await task

    await turn()
    assert seen == {"who": "turn-1", "late": "added-after"}


@pytest.mark.asyncio
async def test_coordinator_properties_isolate_across_concurrent_turns():
    """The same assertion, through TurnCoordinator's public attributes."""
    from unittest.mock import MagicMock

    from src.core.orchestration.turn_coordinator import TurnCoordinator

    tc = TurnCoordinator(
        tool_registry=MagicMock(), tool_executor=MagicMock(),
        memory_engine=None, event_bus=None,
    )
    started = asyncio.Event()

    async def turn(name: str, hold: bool) -> tuple:
        begin_turn()
        tc._current_turn_mode = name
        tc.last_turn_metrics["who"] = name
        tc._last_tool_selection = {"path": name}
        if hold:
            started.set()
            await asyncio.sleep(0.05)
        else:
            await started.wait()
            await asyncio.sleep(0)
        return (
            tc._current_turn_mode,
            tc.last_turn_metrics["who"],
            tc._last_tool_selection["path"],
        )

    first, second = await asyncio.gather(turn("chat", True), turn("agent", False))
    assert first == ("chat", "chat", "chat")
    assert second == ("agent", "agent", "agent")


def test_reads_outside_a_turn_fall_back_instead_of_crashing():
    """Startup / unrelated-task reads still work (UIStateSyncer getattr defaults)."""
    from unittest.mock import MagicMock

    from src.core.orchestration.turn_coordinator import TurnCoordinator

    turn_scratch_var.set(None)
    tc = TurnCoordinator(
        tool_registry=MagicMock(), tool_executor=MagicMock(),
        memory_engine=None, event_bus=None,
    )
    assert tc.last_turn_metrics == {}
    assert tc.last_turn_thinking_events == []
    assert tc._current_turn_mode == "agent"
    tc.last_turn_metrics["x"] = 1
    assert tc.last_turn_metrics == {"x": 1}
    assert isinstance(tc._scratch, TurnScratch)
