"""Regression tests for ConversationOrchestrator per-session turn state (issue #208).

A single orchestrator instance serves concurrent turns whose session ids differ (a
user GUI turn vs a cron/heartbeat/webhook autonomous turn). Turn state must be keyed
by the session resolved from ``session_id_var`` so a background turn starting mid-flight
cannot clobber the user turn's ``current_turn`` / pending tools / transcript — which
previously misrouted or dropped the user's reply.
"""

import asyncio

import pytest

from src.core.orchestration.conversation_orchestrator import (
    ConversationOrchestrator,
    TurnContext,
    TurnInitiator,
)
from src.core.orchestration.context import session_id_var


def _bare_orchestrator() -> ConversationOrchestrator:
    """Construct just the per-session state slots without the heavy __init__ wiring."""
    o = object.__new__(ConversationOrchestrator)
    o._turns_by_session = {}
    o._pending_calls_by_session = {}
    o._pending_responses_by_session = {}
    o._user_transcript_by_session = {}
    o._model_transcript_by_session = {}
    o._active_task_by_session = {}
    return o


def _turn(turn_id: str, session: str, initiator: TurnInitiator) -> TurnContext:
    return TurnContext(turn_id=turn_id, initiator=initiator, category="chat", session_key=session)


def test_turn_state_isolated_across_concurrent_sessions():
    o = _bare_orchestrator()

    tok_a = session_id_var.set("A")
    o.current_turn = _turn("tA", "A", TurnInitiator.USER)
    o._pending_tool_calls.append({"name": "x"})
    o._current_user_transcript = "userA"
    session_id_var.reset(tok_a)

    # A background (autonomous) turn on a different session must start with a clean slate
    # and must not overwrite session A's state.
    tok_b = session_id_var.set("B")
    assert o.current_turn is None
    assert o._pending_tool_calls == []
    o.current_turn = _turn("tB", "B", TurnInitiator.SYSTEM)
    o._pending_tool_calls.append({"name": "y"})
    o._current_user_transcript = "userB"
    assert o.current_turn.turn_id == "tB"
    assert [c["name"] for c in o._pending_tool_calls] == ["y"]
    assert o._current_user_transcript == "userB"
    session_id_var.reset(tok_b)

    # Back in A's context, everything is exactly as A left it.
    tok_a2 = session_id_var.set("A")
    assert o.current_turn.turn_id == "tA"
    assert [c["name"] for c in o._pending_tool_calls] == ["x"]
    assert o._current_user_transcript == "userA"
    session_id_var.reset(tok_a2)


@pytest.mark.asyncio
async def test_reset_clears_only_the_current_session():
    o = _bare_orchestrator()

    tok_a = session_id_var.set("A")
    o.current_turn = _turn("tA", "A", TurnInitiator.USER)
    o._current_user_transcript = "userA"
    session_id_var.reset(tok_a)

    tok_b = session_id_var.set("B")
    o.current_turn = _turn("tB", "B", TurnInitiator.SYSTEM)
    await o._reset_turn_tracking()  # resets B only
    assert o.current_turn is None
    session_id_var.reset(tok_b)

    tok_a2 = session_id_var.set("A")
    assert o.current_turn is not None and o.current_turn.turn_id == "tA"
    assert o._current_user_transcript == "userA"
    session_id_var.reset(tok_a2)


def test_active_task_tracked_per_session():
    o = _bare_orchestrator()
    sentinel_a = object()
    sentinel_b = object()

    tok_a = session_id_var.set("A")
    o._active_turn_task = sentinel_a
    session_id_var.reset(tok_a)

    tok_b = session_id_var.set("B")
    assert o._active_turn_task is None  # B's stop must not see A's task
    o._active_turn_task = sentinel_b
    assert o._active_turn_task is sentinel_b
    session_id_var.reset(tok_b)

    tok_a2 = session_id_var.set("A")
    assert o._active_turn_task is sentinel_a
    o._active_turn_task = None
    assert o._active_turn_task is None
    session_id_var.reset(tok_a2)
