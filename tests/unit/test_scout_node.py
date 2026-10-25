"""Unit tests for scout gather helpers."""
from src.core.orchestration.nodes.scout_node import (
    build_confirmed_tool_menu,
    validate_plan_tools_against_menu,
)


def test_build_confirmed_tool_menu_includes_schemas_and_handoffs():
    schemas = [
        {"name": "search_for_tool", "description": "Find tools"},
        {"name": "get_datetime", "description": "Current time"},
    ]

    class _Handoff:
        name = "delegate_to_research_agent"
        description = "Research specialist"

    menu = build_confirmed_tool_menu(schemas, [_Handoff()])
    names = {e["name"] for e in menu}
    assert "search_for_tool" in names
    assert "delegate_to_research_agent" in names


def test_validate_plan_tools_against_menu_rejects_unknown():
    menu = [{"name": "delegate_to_research_agent", "description": "Research"}]
    steps = [{"id": "step_1", "tools": ["google_drive_search"], "description": "Find file"}]
    feedback = validate_plan_tools_against_menu(steps, menu)
    assert feedback is not None
    assert "google_drive_search" in feedback


def test_validate_plan_tools_against_menu_passes_allowed():
    menu = [{"name": "delegate_to_traversal_agent", "description": "Traversal"}]
    steps = [{"id": "step_1", "tools": ["delegate_to_traversal_agent"], "description": "Read file"}]
    assert validate_plan_tools_against_menu(steps, menu) is None


def test_planning_turn_tool_selection_non_empty(monkeypatch):
    """Planning turns must still run the selector (tools carry forward post-approval)."""
    from src.infrastructure.tools.registry import ToolRegistry
    from unittest.mock import MagicMock

    registry = ToolRegistry()
    registry.discover_all()
    # Exercise planning dispatch without letting the developer's running model
    # server turn this test into real model loading/inference.
    select = MagicMock(return_value=["search_for_tool", "get_datetime", "web_search"])
    monkeypatch.setattr(registry._llm_selector, "is_available", lambda: True)
    monkeypatch.setattr(registry._llm_selector, "select", select)
    result = registry.get_schemas_for_turn(
        "make a plan to summarize my unread work emails",
        category="planning",
    )
    names = result.get("selection", {}).get("final_tool_names") or []
    assert len(names) >= 3, f"Expected non-trivial tool menu for planning turn, got: {names}"
    assert "search_for_tool" in names
    select.assert_called_once()
