from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from src.core.discovery.feed_context import FEED_TOOL_NAMES
from src.core.orchestration.nodes.graphrag_node import make_graphrag_node
from src.core.orchestration.nodes.supervisor_node import make_supervisor_node
from src.core.orchestration.turn.mode_resolver import TurnModeResolver
from src.core.orchestration.turn.pipeline import TurnPipeline
from src.core.orchestration.turn.state_builder import TurnStateBuilder
from src.core.orchestration.turn.tool_selector import TurnToolSelector


def test_feed_prompt_reads_only_interest_memory():
    profile = SimpleNamespace(topics={"fictional_topic": 1.5}, categories={"articles": 1},
                              blocked_domains=[], feedback_history=["PRIVATE_FEEDBACK"])
    with patch("src.core.discovery.feed_context.InterestTuningEngine") as tuning, \
         patch("src.core.orchestration.turn.state_builder.get_domain_persona") as persona:
        tuning.return_value.get_profile.return_value = profile
        memory = MagicMock()
        prompt = TurnStateBuilder.build_system_instruction(
            category="feed", turn_mode="agent", initiator="system", platform="background",
            prompt="Write a read", history=[{"content": "PRIVATE_CHAT"}], memory_engine=memory,
        )
    assert "fictional_topic" in prompt
    assert "PRIVATE" not in prompt
    memory.get_memory_block.assert_not_called()
    persona.assert_not_called()


@pytest.mark.asyncio
async def test_feed_fixed_tools_bypass_selector_and_checkpoint_tools():
    registry = MagicMock()
    registry._tools = {name: {"name": name} for name in FEED_TOOL_NAMES}
    selector = TurnToolSelector(registry)
    selector._get_schema_for_tool = lambda name: {"name": name}
    engine = MagicMock()
    selection = await selector.select("proceed", category="feed", session_id="old-chat",
                                      langgraph_engine=engine, history=[{"content": "PRIVATE"}])
    assert {schema["name"] for schema in selection.schemas} == set(FEED_TOOL_NAMES)
    registry.get_schemas_for_turn.assert_not_called()
    engine.get_checkpoint_tools.assert_not_called()
    resolver = TurnModeResolver()
    assert await resolver.resolve(prompt="plan", category="feed", content_blocks=None,
                                  image_base64=None, history=[], initiator=None, platform="pc") == (
                                      "agent", "feed_fixed", None)
    assert resolver.category_for_turn("feed", "agent") == "feed"


@pytest.mark.asyncio
async def test_feed_omits_general_retrieval():
    clients = [MagicMock() for _ in range(3)]
    node = make_graphrag_node(clients[0], activity_client=clients[1], drive_client=clients[2])
    result = await node({"category": "feed", "messages": [HumanMessage(content="article")],
                         "system_instruction": "compact"}, config={})
    assert result == {"system_instruction": "compact"}
    for client in clients:
        client.search.assert_not_called()


@pytest.mark.asyncio
async def test_feed_final_model_request_has_no_extra_tools_or_catalogs():
    captured = {}
    def respond(prompt):
        captured["messages"] = prompt.to_messages()
        return AIMessage(content="A useful short read")
    model = MagicMock()
    model.bind_tools.return_value = RunnableLambda(respond)
    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = lambda name: SimpleNamespace(name=name)
    node = make_supervisor_node(model, {"code_agent": object()}, registry,
                                [SimpleNamespace(name="delegate_to_code_agent")])
    with patch("src.core.orchestration.nodes.supervisor_node.build_capability_directory") as catalog, \
         patch("src.core.orchestration.nodes.supervisor_node.build_active_mcp_roster") as roster:
        await node({"category": "feed", "system_instruction": "compact interests",
                    "messages": [HumanMessage(content="Write this read")],
                    "tools": [{"name": name} for name in (*FEED_TOOL_NAMES, "read_gmail")]
                    }, config={})
    bound = model.bind_tools.call_args.args[0]
    assert {tool.name for tool in bound} == set(FEED_TOOL_NAMES)
    catalog.assert_not_called()
    roster.assert_not_called()
    assert captured["messages"][0].content.strip() == "compact interests"


@pytest.mark.asyncio
async def test_feed_never_reuses_chat_checkpoint():
    pipeline = object.__new__(TurnPipeline)
    pipeline.category = "feed"
    pipeline._c = MagicMock()
    pipeline._c._langgraph_engine.get_history = AsyncMock(return_value=["PRIVATE_CHAT"])
    assert await pipeline._thread_has_checkpoint() is False
    pipeline._c._langgraph_engine.get_history.assert_not_called()
