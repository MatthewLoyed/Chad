import pytest
from unittest.mock import MagicMock
from src.core.orchestration.turn.event_capture import TurnEventCapture, consolidate_thoughts
from src.application.events.events import ToolsMenuEvent, ExecutionPlanEvent


def test_turn_event_capture_subscribes_and_captures_tools_menu():
    event_bus = MagicMock()
    subscriptions = {}

    def mock_subscribe(event_type, handler):
        subscriptions[event_type] = handler

    def mock_unsubscribe(event_type, handler):
        subscriptions.pop(event_type, None)

    event_bus.subscribe.side_effect = mock_subscribe
    event_bus.unsubscribe.side_effect = mock_unsubscribe

    with TurnEventCapture(event_bus) as capture:
        assert ToolsMenuEvent in subscriptions
        assert ExecutionPlanEvent in subscriptions

        # Simulate ToolsMenuEvent
        tools_event = ToolsMenuEvent(
            tools=[{"name": "fetch_web_page", "description": "Fetches page"}],
            shortlist_size=5,
            selection_path="hybrid",
        )
        subscriptions[ToolsMenuEvent](tools_event)

        # Simulate ExecutionPlanEvent
        plan_event = ExecutionPlanEvent(
            plan=[{"id": "step_1", "description": "Do something"}],
            plan_id="plan-123",
            goal="Test goal",
        )
        subscriptions[ExecutionPlanEvent](plan_event)

    # After exit, handlers should be unsubscribed
    assert len(subscriptions) == 0

    consolidated = capture.consolidate()
    assert len(consolidated) == 2

    # Verify tool_menu
    menu = consolidated[0]
    assert menu["type"] == "tool_menu"
    assert len(menu["tools"]) == 1
    assert menu["tools"][0]["name"] == "fetch_web_page"
    assert menu["shortlist_size"] == 5
    assert menu["selection_path"] == "hybrid"

    # Verify execution_plan
    plan = consolidated[1]
    assert plan["type"] == "execution_plan"
    assert plan["plan_id"] == "plan-123"
    assert plan["goal"] == "Test goal"
