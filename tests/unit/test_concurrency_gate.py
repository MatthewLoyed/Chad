import asyncio
import pytest
from src.core.orchestration.conversation.concurrency_gate import TurnConcurrencyGate


@pytest.mark.asyncio
async def test_user_turn_runs_immediately():
    gate = TurnConcurrencyGate()
    executed = False
    async with gate.turn_guard(is_background=False, is_local=False, session_id="user-1"):
        executed = True
    assert executed is True


@pytest.mark.asyncio
async def test_background_turn_waits_for_active_user_turn():
    gate = TurnConcurrencyGate()
    order = []

    async def user_turn():
        async with gate.turn_guard(is_background=False, is_local=False, session_id="user-1"):
            order.append("user_start")
            await asyncio.sleep(0.05)
            order.append("user_end")

    async def bg_turn():
        await asyncio.sleep(0.01)  # Ensure user_turn starts first
        async with gate.turn_guard(is_background=True, is_local=False, session_id="bg-1"):
            order.append("bg_run")

    await asyncio.gather(user_turn(), bg_turn())
    assert order == ["user_start", "user_end", "bg_run"]


@pytest.mark.asyncio
async def test_user_turn_preempts_queued_background_turns():
    gate = TurnConcurrencyGate()
    order = []

    async def first_bg_turn():
        async with gate.turn_guard(is_background=True, is_local=True, session_id="bg-1"):
            order.append("bg1_start")
            await asyncio.sleep(0.05)
            order.append("bg1_end")

    async def second_bg_turn():
        await asyncio.sleep(0.01)  # Enter while bg1 is running
        async with gate.turn_guard(is_background=True, is_local=True, session_id="bg-2"):
            order.append("bg2_run")

    async def user_turn():
        await asyncio.sleep(0.02)  # Enter after bg2 is waiting
        async with gate.turn_guard(is_background=False, is_local=True, session_id="user-1"):
            order.append("user_run")

    await asyncio.gather(first_bg_turn(), second_bg_turn(), user_turn())
    # User turn has priority over second background turn and should run before bg2
    assert order == ["bg1_start", "bg1_end", "user_run", "bg2_run"]


@pytest.mark.asyncio
async def test_local_model_mutual_exclusion():
    gate = TurnConcurrencyGate()
    running = []
    max_concurrent = 0

    async def turn(name: str):
        nonlocal max_concurrent
        async with gate.turn_guard(is_background=False, is_local=True, session_id=name):
            running.append(name)
            max_concurrent = max(max_concurrent, len(running))
            await asyncio.sleep(0.02)
            running.remove(name)

    await asyncio.gather(*(turn(f"turn-{i}") for i in range(5)))
    assert max_concurrent == 1
