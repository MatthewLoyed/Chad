"""Regression: plan approval follow-ups must not become plan_goal / re-approval thrash."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from src.core.orchestration.nodes.planner_node import _first_step_id, _normalize_plan_steps
from src.core.orchestration.nodes.routing import route_after_planner, route_entry
from src.core.orchestration.plan_service import PlanService, is_plan_followup_message


def test_bare_proceed_is_followup():
    assert is_plan_followup_message("proceed")
    assert is_plan_followup_message("Proceed")
    assert is_plan_followup_message("ok")


def test_original_user_ask_is_not_followup():
    assert not is_plan_followup_message(
        "PLAN MODE. Find my Google Drive wedding roadmap and build a site."
    )


def test_eval_approval_nudge_is_followup():
    assert is_plan_followup_message(
        "Approved — execute the existing plan now. Do not create a new plan. "
        "Call the tools/delegates for each step and call complete_step as you finish."
    )


def test_resolve_goal_skips_proceed_and_keeps_plan_goal():
    state = {
        "plan_goal": "Build the wedding website from the Drive roadmap.",
        "messages": [
            HumanMessage(content="Build the wedding website from the Drive roadmap."),
            HumanMessage(content="proceed"),
        ],
    }
    assert PlanService.resolve_goal(state) == "Build the wedding website from the Drive roadmap."
    assert PlanService.resolve_goal(state, goal="proceed") == (
        "Build the wedding website from the Drive roadmap."
    )


def test_resolve_goal_skips_proceed_when_scanning_messages():
    state = {
        "messages": [
            HumanMessage(content="Research Tidepool and write a brief."),
            HumanMessage(content="proceed"),
        ],
    }
    assert PlanService.resolve_goal(state) == "Research Tidepool and write a brief."


def test_route_entry_continues_active_plan_even_in_planning_category(monkeypatch):
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(
        ConfigManager,
        "get",
        staticmethod(lambda key, default=None: True if key == "planner.enabled" else default),
    )
    state = {
        "category": "planning",
        "turn_mode": "plan",
        "active_plan": [
            {"id": "step_1", "description": "do work", "status": "pending", "tools": []},
        ],
        "messages": [HumanMessage(content="proceed")],
    }
    assert route_entry(state) == "supervisor"


def test_route_after_planner_skips_approval_on_autonomous_replan():
    state = {
        "plan_replanned": True,
        "active_plan": [{"id": "step_1", "status": "pending"}],
        "messages": [HumanMessage(content="proceed")],
    }
    assert route_after_planner(state) == "supervisor"


def test_normalize_plan_steps_drops_string_junk_from_json_repair():
    # HITL 2026-07-24: json_repair yielded ["Any text"] then
    # `normalized or plan_list` restored the string and crashed on plan_list[0]["id"].
    assert _normalize_plan_steps(["Any text"]) == []
    assert _normalize_plan_steps(None) == []
    steps = _normalize_plan_steps(
        [{"description": "do the thing", "tools": ["write_file"]}]
    )
    assert steps[0]["id"] == "step_1"
    assert steps[0]["status"] == "pending"
    assert _first_step_id(steps) == "step_1"
    assert _first_step_id(["Any text"]) == ""  # type: ignore[list-item]
    assert _first_step_id([]) == ""


def test_failure_context_routes_to_supervisor_not_scout():
    from langchain_core.messages import AIMessage

    from src.core.orchestration.nodes.routing import make_route_after_supervisor

    route = make_route_after_supervisor({})
    state = {
        "failure_context": "Step step_4 stalled — try write_file instead of file_manager.",
        "active_plan": [{"id": "step_4", "status": "running", "description": "save site"}],
        "iteration_count": 3,
        "messages": [AIMessage(content="rethinking")],
    }
    assert route(state) == "supervisor"


def test_failure_context_with_tool_calls_still_runs_tools():
    from langchain_core.messages import AIMessage

    from src.core.orchestration.nodes.routing import make_route_after_supervisor

    route = make_route_after_supervisor({})
    state = {
        "failure_context": "prior stall",
        "iteration_count": 2,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": "write_file", "args": {}, "id": "1"}],
            )
        ],
    }
    assert route(state) == "tools"


def test_step_recovery_feedback_mentions_artifacts_on_safety_lock():
    from src.core.orchestration.nodes.supervisor_node import _build_step_recovery_feedback
    from langchain_core.messages import ToolMessage

    feedback = _build_step_recovery_feedback(
        {"id": "step_4", "description": "Save the website"},
        [
            ToolMessage(
                content="SAFETY LOCK: Missing approval_token. Call propose_file_plan.",
                name="file_manager",
                tool_call_id="x",
            )
        ],
        reason="20 supervisor tool-turns without complete_step",
    )
    assert "create_plan" in feedback.lower() or "Do NOT" in feedback or "do NOT" in feedback
    assert "write_file" in feedback or "build_html_file" in feedback
    assert "step_4" in feedback
