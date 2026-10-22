from langchain_core.messages import HumanMessage

from src.core.orchestration.nodes.routing import (
    route_after_planner,
    route_after_scout,
    route_entry,
)


def test_route_entry_chat_lane():
    state = {
        "category": "agent",
        "turn_mode": "chat",
        "messages": [HumanMessage(content="hi")],
    }
    assert route_entry(state) == "conversational"


def test_route_entry_planning_lane_falls_back_when_planner_disabled(monkeypatch):
    """planner.enabled defaults to False (#219/#402 public-release feature gate) —
    a plan-lane request falls back to the normal agent lane instead of planner."""
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: default))
    state = {
        "category": "planning",
        "turn_mode": "plan",
        "messages": [HumanMessage(content="build me a website")],
    }
    assert route_entry(state) == "supervisor"


def test_route_entry_planning_lane_routes_to_planner_when_enabled(monkeypatch):
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(lambda key, default=None: True if key == "planner.enabled" else default),
    )
    state = {
        "category": "planning",
        "turn_mode": "plan",
        "messages": [HumanMessage(content="build me a website")],
    }
    assert route_entry(state) == "planner"


def test_route_entry_agent_defaults_to_supervisor():
    state = {
        "category": "agent",
        "turn_mode": "agent",
        "messages": [HumanMessage(content="search my email")],
    }
    assert route_entry(state) == "supervisor"


def test_route_after_planner_dispatches_scout_on_gather_request():
    state = {
        "scout_gather_request": "Find the latest unread work emails",
        "active_plan": None,
        "messages": [HumanMessage(content="summarize my email")],
    }
    assert route_after_planner(state) == "scout"


def test_route_after_planner_ends_on_pending_approval():
    state = {
        "pending_approval": {"tool_name": "ask_user_question"},
        "messages": [HumanMessage(content="make a website")],
    }
    assert route_after_planner(state) == "__end__"


def test_route_after_planner_approval_wait_when_plan_ready():
    state = {
        "active_plan": [{"id": "step_1", "status": "pending"}],
        "messages": [HumanMessage(content="make a website")],
    }
    assert route_after_planner(state) == "approval_wait"


def test_route_after_scout_returns_to_planner():
    state = {"gather_brief": "facts…", "messages": []}
    assert route_after_scout(state) == "planner"
