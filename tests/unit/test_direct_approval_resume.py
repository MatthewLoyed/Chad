"""Real LangGraph checkpoint/event-stream approval continuation contract."""
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.core.orchestration import approval_store
from src.core.orchestration.approval_gate import evaluate_approval_gate
from src.core.orchestration.context import session_id_var, pending_approval_var
from src.core.orchestration.graph.event_stream import run_turn_stream
from src.core.orchestration.graph.state import ChadState
from src.core.orchestration.nodes.tool_node import make_tool_node
from langgraph.prebuilt import ToolNode


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("decision", ["approved", "declined"])
async def test_approval_executes_exact_call_before_model_and_keeps_stream(decision, nested):
    order = []

    @tool
    async def gated_fixture(recipient: str, body: str) -> dict:
        """Fake external action; never sends anything."""
        gate = evaluate_approval_gate("gated_fixture", params={"recipient": recipient, "body": body})
        if gate.short_circuit is not None:
            return gate.short_circuit
        order.append(("execute", recipient, body))
        return {"success": True, "result": "receipt-123"}

    @tool
    async def sibling_fixture() -> dict:
        """Independent already completed operation."""
        order.append(("sibling",))
        return {"success": True}

    async def model(state, config):
        order.append(("model",))
        outputs = [json.loads(m.content) for m in state["messages"] if isinstance(m, ToolMessage)]
        if decision == "approved":
            assert outputs[-1]["result"] == "receipt-123"
        else:
            assert outputs[-1]["_approval_declined"]
        await adispatch_custom_event("agent_thinking", {"thinking": "Reading receipt", "agent_name": "Supervisor"}, config=config)
        return {"messages": [AIMessage(content="Finished")], "final_response": "Finished"}

    atomic_message = AIMessage(content="", tool_calls=[
        {"name": "gated_fixture", "args": {"recipient": "exact@example.com", "body": "exact body"}, "id": "gated-original"},
        {"name": "sibling_fixture", "args": {}, "id": "sibling-original"},
    ])
    graph_builder = StateGraph(ChadState)
    graph_builder.add_node("tools", make_tool_node(ToolNode([gated_fixture, sibling_fixture])))
    graph_builder.add_node("supervisor", model)
    if nested:
        # A specialist's atomic calls are not in the supervisor checkpoint.
        inner = StateGraph(ChadState)
        inner.add_node("tools", make_tool_node(ToolNode([gated_fixture, sibling_fixture])))
        inner.set_entry_point("tools")
        inner.add_edge("tools", END)
        specialist = inner.compile()

        async def delegate(state, config):
            result = await specialist.ainvoke({"messages": [atomic_message]}, config)
            return {"messages": [ToolMessage(content=json.dumps({"result": "Specialist awaiting approval"}), tool_call_id="delegate")],
                    "pending_approval": result["pending_approval"]}

        graph_builder.add_node("delegate", delegate)
        graph_builder.set_entry_point("delegate")
        graph_builder.add_edge("delegate", END)
    else:
        graph_builder.set_entry_point("tools")
    graph_builder.add_conditional_edges("tools", lambda s: END if s.get("pending_approval") else "supervisor")
    graph_builder.add_edge("supervisor", END)
    graph = graph_builder.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "direct-approval-" + decision}}
    session = session_id_var.set(config["configurable"]["thread_id"])
    pending_ctx = pending_approval_var.set(None)
    pending = None
    try:
        initial = {"messages": [AIMessage(content="", tool_calls=[
            {"name": "delegate_to_browser_agent", "args": {"task": "Do the task"}, "id": "delegate"}
        ]) if nested else atomic_message]}
        first = await run_turn_stream(graph, config, initial, config["configurable"]["thread_id"])
        pending = first["pending_approval"]
        saved = await graph.aget_state(config)
        assert saved.values["pending_approval"]["token"] == pending["token"]
        assert pending["tool_call"]["args"] == {"recipient": "exact@example.com", "body": "exact body"}
        assert order == [("sibling",)]  # no model before approval
        approval_store.resolve(pending["token"], decision)
        pending_approval_var.set(None)
        thinking = AsyncMock()
        result = await run_turn_stream(graph, config, {
            "messages": [HumanMessage(content=decision)], "pending_approval": None,
            "final_response": None, "turn_id": "fresh-turn",
        }, config["configurable"]["thread_id"], on_thinking=thinking)
        assert result["pending_approval"] is None
        assert result["final_response"] == "Finished"
        expected = [("sibling",)]
        if decision == "approved":
            expected.append(("execute", "exact@example.com", "exact body"))
        assert order == expected + [("model",)]
        thinking.assert_awaited_once()
        assert approval_store.peek(pending["token"]) is None
    finally:
        if pending:
            approval_store.consume(pending["token"])
        pending_approval_var.reset(pending_ctx)
        session_id_var.reset(session)


@pytest.mark.asyncio
async def test_pipeline_bypasses_all_preexecution_model_work_on_approval():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.core.orchestration.turn.pipeline import TurnPipeline
    from src.core.orchestration.turn.scratch import TurnScratch
    engine = SimpleNamespace(get_approval_resume_state=AsyncMock(return_value={
        "pending_approval": {"tool_call": {"name": "fixture"}},
        "tools": [{"name": "fixture"}], "system_instruction": "saved context",
    }))
    pipeline = TurnPipeline.__new__(TurnPipeline)
    pipeline._c = SimpleNamespace(_langgraph_engine=engine)
    pipeline.session_id = "same-session"
    pipeline.context = SimpleNamespace(category="planning")
    pipeline.category = "planning"
    pipeline.scratch = TurnScratch()
    pipeline.prompt = "approved"
    pipeline.content_blocks = None
    pipeline.image_base64 = None
    pipeline._prepare = MagicMock(return_value=None)
    pipeline._try_voice_fast_lane = AsyncMock(return_value=None)
    pipeline._try_voice_quick_tool_lane = AsyncMock(return_value=None)
    pipeline._resolve_mode = AsyncMock(side_effect=AssertionError("router must not run"))
    pipeline._resolve_tools = AsyncMock(side_effect=AssertionError("selector must not run"))
    pipeline._prepare_graph = AsyncMock(side_effect=AssertionError("compaction must not run"))
    pipeline._build_prompt = MagicMock(side_effect=AssertionError("must retain saved context"))
    pipeline._open_span = MagicMock()
    pipeline._invoke = AsyncMock(return_value="receipt")
    assert await pipeline.run() == "receipt"
    assert pipeline.sys_instr == "saved context"
    assert pipeline.category == "agent"
    pipeline._resolve_tools.assert_not_awaited()
    pipeline._resolve_mode.assert_not_awaited()
    pipeline._prepare_graph.assert_not_awaited()
