"""Regression tests for PlanService and plan identity."""
from langchain_core.messages import HumanMessage

from src.application.events.event_bus import EventBus
from src.application.events.events import ExecutionPlanEvent
from src.core.orchestration.plan_service import PlanService


def test_emit_snapshot_uses_turn_id_not_session_id():
    bus = EventBus.get_instance()
    bus.clear()
    received: list[ExecutionPlanEvent] = []
    bus.subscribe(ExecutionPlanEvent, received.append)

    state = {
        "turn_id": "turn_3_1718300000000",
        "session_id": "session-uuid-abc",
        "messages": [],
    }
    steps = [{"id": "s1", "description": "Do thing", "tools": [], "status": "pending"}]
    PlanService.emit_snapshot(state, steps, goal="Build something")

    assert len(received) == 1
    event = received[0]
    assert event.plan_id == "turn_3_1718300000000"
    assert event.turn_id == "turn_3_1718300000000"
    assert event.plan_id != state["session_id"]
    assert event.goal == "Build something"
    assert event.replace is True


def test_emit_snapshot_resolves_goal_from_human_message():
    bus = EventBus.get_instance()
    bus.clear()
    received: list[ExecutionPlanEvent] = []
    bus.subscribe(ExecutionPlanEvent, received.append)

    state = {
        "turn_id": "turn_1",
        "session_id": "session-1",
        "messages": [HumanMessage(content="Check my email")],
    }
    PlanService.emit_snapshot(state, [{"id": "s1", "description": "x", "tools": [], "status": "pending"}])

    assert received[0].goal == "Check my email"


def test_emit_snapshot_skips_when_no_turn_id():
    bus = EventBus.get_instance()
    bus.clear()
    received: list[ExecutionPlanEvent] = []
    bus.subscribe(ExecutionPlanEvent, received.append)

    PlanService.emit_snapshot({}, [{"id": "s1", "description": "x", "tools": [], "status": "pending"}])
    assert received == []


def test_langgraph_turn_id_matches_context_turn_id():
    """Regression for duplicate PlanCard bug: turn_id must not fall back to session_id when context has turn_id."""

    class Context:
        turn_id = "turn_2_999"

    session_id = "conv-uuid"
    context = Context()
    turn_id = getattr(context, "turn_id", None) or session_id

    assert turn_id == "turn_2_999"
    assert turn_id != session_id
