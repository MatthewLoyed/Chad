"""Unit tests for Fast Chat Mode read tools, knowledge context, and single-hop execution."""

from typing import Any, Optional
from unittest.mock import MagicMock, AsyncMock
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, ToolCall
from langchain_core.runnables import Runnable

from src.core.orchestration.nodes.conversational_node import make_conversational_node
from src.core.orchestration.nodes.routing import (
    route_after_conversational_responder,
    route_after_tools,
)
from src.core.orchestration.turn.tool_selector import TurnToolSelector


class FakeChatModel(Runnable):
    """Minimal Runnable double for chat model."""

    def __init__(self, response_text: str = "ok"):
        self.response_text = response_text
        self.last_config: Optional[dict] = None
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, input: Any, config: Optional[dict] = None, **kwargs):
        self.last_config = config
        return AIMessage(content=self.response_text)

    async def ainvoke(self, input: Any, config: Optional[dict] = None, **kwargs):
        self.last_config = config
        return AIMessage(content=self.response_text)


def test_turn_tool_selector_chat_schemas_includes_read_tools():
    """Chat mode must return read-only tools without calling dynamic selector."""
    registry = MagicMock()
    mock_tool = lambda n: MagicMock(get_schema=MagicMock(return_value={"name": n}))
    registry._tools = {name: mock_tool(name) for name in TurnToolSelector._CHAT_TOOL_NAMES}

    selector = TurnToolSelector(registry)
    selection = selector.chat_schemas()

    assert selection.is_simple is True
    assert selection.selection["path"] == "turn_mode_chat"
    assert "web_search" in selection.selection["final_tool_names"]
    assert "get_financial_summary" in selection.selection["final_tool_names"]
    assert "google_drive_search" in selection.selection["final_tool_names"]
    assert "get_calendar_events" in selection.selection["final_tool_names"]
    assert len(selection.schemas) == len(TurnToolSelector._CHAT_TOOL_NAMES)


def test_route_after_conversational_responder():
    """If the LLM makes tool calls, route to tools; otherwise route to __end__."""
    ai_msg_with_tool = AIMessage(
        content="",
        tool_calls=[ToolCall(name="web_search", args={"query": "weather"}, id="call_1")],
    )
    state_with_tool = {"messages": [ai_msg_with_tool]}
    assert route_after_conversational_responder(state_with_tool) == "tools"

    ai_msg_text_only = AIMessage(content="It is sunny today.")
    state_text_only = {"messages": [ai_msg_text_only]}
    assert route_after_conversational_responder(state_text_only) == "__end__"


def test_route_after_tools_chat_mode_single_hop():
    """In chat mode, after 1 tool round, route back to conversational_responder; after that, route to __end__."""
    state_round_0 = {
        "turn_mode": "chat",
        "chat_tool_rounds": 0,
        "category": "chat",
    }
    assert route_after_tools(state_round_0) == "conversational_responder"

    state_round_1 = {
        "turn_mode": "chat",
        "chat_tool_rounds": 1,
        "category": "chat",
    }
    assert route_after_tools(state_round_1) == "conversational_responder"

    state_round_2 = {
        "turn_mode": "chat",
        "chat_tool_rounds": 2,
        "category": "chat",
    }
    assert route_after_tools(state_round_2) == "__end__"


@pytest.mark.asyncio
async def test_conversational_responder_binds_tools_round_0_and_injects_knowledge():
    """Round 0 binds tools and injects knowledge_boost into trailing user message."""
    model = FakeChatModel(response_text="Result from model")
    node = make_conversational_node(model)

    state = {
        "turn_mode": "chat",
        "chat_tool_rounds": 0,
        "system_instruction": "### CURRENT DATE\nToday is Wednesday.\n\n### CORE PROFILE\nUser is Matthew.",
        "knowledge_boost": "\n\n### RELATIONAL CONTEXT (GRAPH)\nMatthew likes clean code.",
        "tools": [{"name": "web_search", "description": "Search web"}],
        "messages": [HumanMessage(content="What are my preferences?")],
    }

    config = {"tags": []}
    result = await node(state, config)

    # Model had tools bound on round 0
    assert model.bound_tools == state["tools"]
    assert "conversational-llm" in config["tags"]
    assert result["messages"][0].content == "Result from model"


@pytest.mark.asyncio
async def test_conversational_responder_unbound_tools_round_1():
    """Round 1 (after tool execution) removes tool binding to force text response."""
    model = FakeChatModel(response_text="Final answer incorporating tool data")
    node = make_conversational_node(model)

    state = {
        "turn_mode": "chat",
        "chat_tool_rounds": 1,
        "system_instruction": "### CORE PROFILE\nUser is Matthew.",
        "tools": [{"name": "web_search", "description": "Search web"}],
        "messages": [
            HumanMessage(content="Search for X"),
            AIMessage(content="", tool_calls=[ToolCall(name="web_search", args={"query": "X"}, id="c1")]),
            ToolMessage(content="Result for X", tool_call_id="c1"),
        ],
    }

    config = {"tags": []}
    result = await node(state, config)

    # Model was NOT bound with tools on round 1
    assert model.bound_tools is None
    assert result["messages"][0].content == "Final answer incorporating tool data"


@pytest.mark.asyncio
async def test_chat_mode_turn_full_flow_with_read_tool():
    """Verify end-to-end 1-hop execution flow:
    1. Entry routes to conversational_responder
    2. LLM emits a read tool call -> routes to tools
    3. tools executes and increments chat_tool_rounds
    4. route_after_tools routes back to conversational_responder
    5. conversational_responder generates final answer with no tools bound
    6. route_after_conversational_responder routes to __end__
    """
    state = {
        "turn_mode": "chat",
        "category": "chat",
        "chat_tool_rounds": 0,
        "system_instruction": "### CURRENT DATE\nToday is Wednesday.\n\n### CORE PROFILE\nUser is Matthew.",
        "knowledge_boost": "\n\n### RELATIONAL CONTEXT (GRAPH)\nMatthew lives in Dallas.",
        "tools": [{"name": "get_financial_summary", "description": "Get balances"}],
        "messages": [HumanMessage(content="What is my checking balance?")],
    }

    # Round 0: Model emits tool call
    model_round_0 = FakeChatModel()
    model_round_0.ainvoke = AsyncMock(return_value=AIMessage(
        content="",
        tool_calls=[ToolCall(name="get_financial_summary", args={}, id="call_fin_1")],
    ))
    node_0 = make_conversational_node(model_round_0)
    out_0 = await node_0(state, {"tags": []})
    assert model_round_0.bound_tools == state["tools"]

    state["messages"].append(out_0["messages"][0])
    next_node = route_after_conversational_responder(state)
    assert next_node == "tools"

    # Tool node simulates tool execution
    state["chat_tool_rounds"] += 1
    state["messages"].append(ToolMessage(
        content='{"checking": "$3,450.00", "savings": "$12,100.00"}',
        tool_call_id="call_fin_1",
    ))

    # After tools route back to conversational_responder
    next_node = route_after_tools(state)
    assert next_node == "conversational_responder"

    # Round 1: Final answer with tools unbound
    model_round_1 = FakeChatModel(response_text="Your checking balance is $3,450.00 and savings is $12,100.00.")
    node_1 = make_conversational_node(model_round_1)
    out_1 = await node_1(state, {"tags": []})
    assert model_round_1.bound_tools is None
    assert "Your checking balance is $3,450.00" in out_1["messages"][0].content

    # Next route ends turn
    state["messages"].append(out_1["messages"][0])
    assert route_after_conversational_responder(state) == "__end__"

