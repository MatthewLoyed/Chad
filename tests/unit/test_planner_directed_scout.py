"""Unit tests for planner-directed Scout helpers, propose_plan, and plan-step hygiene."""

from unittest.mock import AsyncMock, patch

from src.core.orchestration.nodes.planner_node import (
    _build_propose_plan_tool,
    _strip_forbidden_plan_tools,
    make_planner_node,
)
from src.core.orchestration.nodes.scout_node import (
    _SCOUT_BLOCKED_TOOLS,
    validate_plan_tools_against_menu,
)
from src.tools.system.dispatch_scout import DispatchScoutTool
from src.tools.system.propose_plan import ProposePlanTool


def test_strip_forbidden_plan_tools_removes_ask_dispatch_and_propose():
    plan = [
        {
            "id": "step_1",
            "description": "Ask the user",
            "tools": ["ask_user_question", "delegate_to_code_agent"],
            "status": "pending",
        },
        {
            "id": "step_2",
            "description": "Gather",
            "tools": ["dispatch_scout", "pause_for_input", "propose_plan"],
            "status": "pending",
        },
    ]
    cleaned = _strip_forbidden_plan_tools(plan)
    assert cleaned[0]["tools"] == ["delegate_to_code_agent"]
    assert cleaned[1]["tools"] == []


def test_validate_plan_rejects_interactive_tools_even_without_menu():
    plan = [{"id": "step_1", "tools": ["ask_user_question"], "status": "pending"}]
    feedback = validate_plan_tools_against_menu(plan, None)
    assert feedback is not None
    assert "ask_user_question" in feedback


def test_validate_plan_rejects_propose_plan_on_steps():
    plan = [{"id": "step_1", "tools": ["propose_plan"], "status": "pending"}]
    feedback = validate_plan_tools_against_menu(plan, None)
    assert feedback is not None
    assert "propose_plan" in feedback


def test_scout_blocks_ask_user_question():
    assert "ask_user_question" in _SCOUT_BLOCKED_TOOLS
    assert "dispatch_scout" in _SCOUT_BLOCKED_TOOLS


async def test_dispatch_scout_tool_sets_flag():
    tool = DispatchScoutTool()
    result = await tool.execute(brief="List files in Desktop/Wedding")
    assert result["success"] is True
    assert result["_dispatch_scout"] is True
    assert "Wedding" in result["brief"]


async def test_propose_plan_tool_sets_flag():
    tool = ProposePlanTool()
    steps = [
        {"description": "Build HTML page", "tools": ["delegate_to_code_agent"]},
        {"description": "Present to user", "tools": []},
    ]
    result = await tool.execute(steps=steps)
    assert result["success"] is True
    assert result["_propose_plan"] is True
    assert len(result["steps"]) == 2


async def test_propose_plan_bound_tool_always_named():
    bound = _build_propose_plan_tool()
    assert bound.name == "propose_plan"
    result = await bound.ainvoke(
        {
            "steps": [
                {"description": "Read docs", "tools": ["delegate_to_traversal_agent"]},
            ]
        }
    )
    assert result["_propose_plan"] is True
    assert result["steps"][0]["description"] == "Read docs"


def test_propose_plan_schema_has_no_defs_refs():
    """LM Studio/Gemma cannot resolve $defs — schema must be fully inlined."""
    bound = _build_propose_plan_tool()
    schema = bound.get_input_jsonschema()
    dumped = str(schema)
    assert "$defs" not in schema
    assert "$ref" not in dumped
    items = schema["properties"]["steps"]["items"]
    assert items["type"] == "object"
    assert "description" in items["properties"]
    assert "tools" in items["properties"]


async def test_propose_plan_rejects_empty_steps():
    tool = ProposePlanTool()
    result = await tool.execute(steps=[])
    assert result["success"] is False


async def test_make_planner_node_none_registry_does_not_unboundlocal():
    """Regression: assigning to closure param `tool_registry` made it UnboundLocal."""
    node = make_planner_node(model=object(), tool_registry=None)
    with patch(
        "src.core.orchestration.nodes.planner_node._run_planner_tool_loop",
        new_callable=AsyncMock,
    ) as loop:
        loop.return_value = {"active_plan": None}
        with patch(
            "src.core.intelligence.prompt_builder.SystemPromptBuilder.build",
            return_value="plan",
        ):
            out = await node({"messages": [], "tools": []}, {})
    assert out == {"active_plan": None}
    assert loop.await_args.kwargs["tool_registry"] is not None
    assert hasattr(loop.await_args.kwargs["tool_registry"], "get_langchain_tool_by_name")
