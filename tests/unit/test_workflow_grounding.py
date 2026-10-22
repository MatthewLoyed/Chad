import pytest
import re
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from src.core.orchestration.handoffs import create_subagent_adapter
from src.core.orchestration.nodes.tool_node import make_tool_node
from src.core.orchestration.nodes.supervisor_node import make_supervisor_node


@pytest.fixture(autouse=True)
def set_testing_env(monkeypatch):
    monkeypatch.setenv("CHAD_TESTING", "true")


@pytest.mark.asyncio
async def test_subagent_adapter_clean_isolation():
    mock_subgraph = AsyncMock()
    mock_subgraph.ainvoke.return_value = {
        "messages": [AIMessage(content="Specialist completed task.")]
    }
    mock_subgraph.aget_state.return_value = None

    adapter = create_subagent_adapter("code_agent", mock_subgraph)

    state = {
        "messages": [
            HumanMessage(content="Start"),
            ToolMessage(
                name="get_upcoming_assignments",
                content='{"assignments": [{"name": "Mid Term"}]}',
                tool_call_id="call_prev",
            ),
            AIMessage(
                content="Delegating",
                tool_calls=[{
                    "id": "del_1",
                    "name": "delegate_to_code_agent",
                    "args": {"task": "Build weekly planner HTML with Mid Term on Oct 22"},
                }],
            ),
        ],
        "plan_goal": "Build weekly planner",
    }

    config = {"configurable": {"thread_id": "test_thread"}}
    res = await adapter(state, config)

    assert "messages" in res
    assert mock_subgraph.ainvoke.called
    sub_input = mock_subgraph.ainvoke.call_args[0][0]
    human_msg = sub_input["messages"][-1]
    # Clean slate isolation: specialist receives only the explicit task prompt and system prompt
    assert human_msg.content == "Build weekly planner HTML with Mid Term on Oct 22"
    assert "WORKFLOW CONTEXT & RETRIEVED DATA" not in human_msg.content


from src.core.orchestration.nodes.tool_node import make_tool_node
import json

@pytest.mark.asyncio
async def test_complete_step_saves_summary_in_active_plan():
    mock_base_tool_node = AsyncMock()
    mock_base_tool_node.ainvoke.return_value = {
        "messages": [
            ToolMessage(
                name="complete_step",
                content=json.dumps({
                    "success": True,
                    "_step_complete": True,
                    "step_id": "step_1",
                    "summary": "Retrieved 4 Canvas assignments successfully.",
                    "result": "Step 'step_1' marked as completed.",
                }),
                tool_call_id="c1",
            )
        ]
    }

    tool_node_fn = make_tool_node(mock_base_tool_node)

    plan = [
        {"id": "step_1", "description": "Fetch assignments", "status": "running"},
        {"id": "step_2", "description": "Fetch calendar", "status": "pending"},
    ]
    state = {
        "messages": [
            AIMessage(
                content="Completing step 1",
                tool_calls=[{
                    "id": "c1",
                    "name": "complete_step",
                    "args": {
                        "step_id": "step_1",
                        "summary": "Retrieved 4 Canvas assignments successfully.",
                    },
                }],
            )
        ],
        "active_plan": plan,
        "plan_step": "step_1",
    }

    result = await tool_node_fn(state, {})
    updated_plan = result["active_plan"]
    assert updated_plan[0]["status"] == "completed"
    assert updated_plan[0]["summary"] == "Retrieved 4 Canvas assignments successfully."
    assert result["plan_step"] == "step_2"


from unittest.mock import patch
from src.tools.filesystem.html_builder_tool import HtmlBuilderTool

@pytest.mark.asyncio
async def test_html_auto_save_preserves_task_filename():
    mock_subgraph = AsyncMock()
    # Specialist returns raw HTML
    mock_subgraph.ainvoke.return_value = {
        "messages": [AIMessage(content="<!DOCTYPE html><html><body><h1>Weekly Planner</h1></body></html>")]
    }
    mock_subgraph.aget_state.return_value = None

    adapter = create_subagent_adapter("code_agent", mock_subgraph)

    state = {
        "messages": [
            AIMessage(
                content="Delegating",
                tool_calls=[{
                    "id": "del_html",
                    "name": "delegate_to_code_agent",
                    "args": {"task": "Build weekly planner and save as weekly_planner.html"},
                }],
            ),
        ],
    }

    config = {"configurable": {"thread_id": "test_html_thread"}}
    with patch.object(HtmlBuilderTool, "execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {"success": True, "result": "Saved successfully."}
        res = await adapter(state, config)

    tool_msg = res["messages"][0]
    assert "weekly_planner.html" in tool_msg.content
    assert "NOTE: In Chad, HTML deliverables are packaged directly into the artifacts registry" in tool_msg.content
    assert mock_exec.call_args[1]["filename"] == "weekly_planner.html"


from langchain_core.runnables import RunnableLambda

@pytest.mark.asyncio
async def test_supervisor_node_final_response_grounding_directive():
    captured_messages = []

    async def fake_invoke(prompt_value, config=None):
        msgs = prompt_value.to_messages()
        captured_messages.extend(msgs)
        return AIMessage(content="Final synthesis response.")

    mock_model = MagicMock()
    mock_model.bind_tools.return_value = RunnableLambda(fake_invoke)

    mock_registry = MagicMock()
    mock_registry.get_langchain_tool_by_name.return_value = None

    supervisor_fn = make_supervisor_node(
        model=mock_model,
        sub_agents_dict={},
        tool_registry=mock_registry,
        handoff_tools=[],
    )

    plan = [
        {
            "id": "step_1",
            "description": "Fetch assignments",
            "status": "completed",
            "summary": "Retrieved 4 Canvas assignments.",
        },
        {
            "id": "step_2",
            "description": "Create HTML planner",
            "status": "completed",
            "summary": "Generated weekly_planner.html.",
        },
    ]

    state = {
        "messages": [HumanMessage(content="User prompt")],
        "active_plan": plan,
        "plan_step": "step_2",
        "iteration_count": 5,
        "system_instruction": "You are Chad.",
    }

    res = await supervisor_fn(state, {})
    assert len(captured_messages) > 0
    tail_message = captured_messages[-1]

    # Verify plan lines include summary
    assert "step_1: Fetch assignments -> Result: Retrieved 4 Canvas assignments." in tail_message.content
    assert "step_2: Create HTML planner -> Result: Generated weekly_planner.html." in tail_message.content

    # Verify grounding directive when all steps are completed
    assert "ALL PLAN STEPS COMPLETED — DELIVER FINAL RESPONSE" in tail_message.content
    assert "Base your summary ONLY on the real-world data retrieved by tools" in tail_message.content
    assert "NEVER invent, hallucinate, or substitute placeholder items" in tail_message.content
