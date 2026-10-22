r"""
Unit tests for the voice-lane LangGraph node (epic #291/#293 Phase 3).

Regression coverage for the bug found while implementing #293: voice_responder
never tagged its chain invocation, so langgraph_engine's astream_events loop
treated every voice-lane token as an internal (subagent/planner) stream and
silently suppressed it — Phase 2's sentence streaming was a complete no-op for
the fast voice lane. conversational_node/supervisor_node both tag their runs;
voice_node must do the same.

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_node.py -q
"""

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable

from src.core.orchestration.nodes.voice_node import VOICE_TOOL_NAMES, make_voice_node


class FakeVoiceModel(Runnable):
    """Minimal Runnable double that records the config it was invoked with."""

    def __init__(self):
        self.last_config: Optional[dict] = None
        self.bound_tool_names = None

    def bind_tools(self, tools):
        self.bound_tool_names = [getattr(t, "name", None) for t in tools]
        return self

    def invoke(self, input: Any, config: Optional[dict] = None, **kwargs):
        self.last_config = config
        return AIMessage(content="ok")

    async def ainvoke(self, input: Any, config: Optional[dict] = None, **kwargs):
        self.last_config = config
        return AIMessage(content="ok")


def _base_state(tools=None):
    return {
        "messages": [HumanMessage(content="turn on the lamp")],
        "system_instruction": "",
        "tools": tools or [],
        "iteration_count": 0,
    }


@pytest.mark.asyncio
async def test_voice_responder_tags_config_for_streaming():
    """The whole point of the fix: langgraph_engine only forwards tokens tagged
    'voice-llm' (or 'conversational-llm'/'supervisor-llm') to on_token — without
    this tag, voice replies never stream and only reach the user as one final
    blob at turn end."""
    model = FakeVoiceModel()
    node = make_voice_node(model, tool_registry=MagicMock())

    await node(_base_state(), config={})

    assert model.last_config is not None
    assert "voice-llm" in model.last_config.get("tags", [])


@pytest.mark.asyncio
async def test_voice_responder_preserves_existing_tags():
    """Tagging must extend, not clobber, any tags langgraph already set."""
    model = FakeVoiceModel()
    node = make_voice_node(model, tool_registry=MagicMock())

    await node(_base_state(), config={"tags": ["some-existing-tag"]})

    assert set(model.last_config["tags"]) == {"some-existing-tag", "voice-llm"}


def _registry_with_all_tools():
    def get_tool(name):
        tool = MagicMock()
        tool.name = name
        return tool

    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = get_tool
    return registry


@pytest.mark.asyncio
async def test_voice_responder_binds_only_voice_tool_names():
    """Only hubspace_control/escalate_to_agent are ever bound — the RAG shortlist
    may include other tool schemas, but the voice micro-agent must ignore them
    (fixed tool set is what keeps this lane fast)."""
    model = FakeVoiceModel()

    tools_schema = [
        {"name": "hubspace_control"},
        {"name": "escalate_to_agent"},
        {"name": "web_search"},  # not in VOICE_TOOL_NAMES — must be excluded
    ]
    node = make_voice_node(model, tool_registry=_registry_with_all_tools())

    await node(_base_state(tools=tools_schema), config={})

    assert set(model.bound_tool_names) == VOICE_TOOL_NAMES
    assert "voice-llm" in model.last_config.get("tags", [])


@pytest.mark.asyncio
async def test_voice_tools_bound_even_when_shortlist_omits_them():
    """Regression: the voice tool set is fixed, NOT derived from the RAG shortlist.

    The shortlist is query-driven, so "check my email inbox" surfaces Gmail tools
    and no escalate_to_agent. Binding only the shortlist's intersection left the
    model with zero tools while the prompt still told it to escalate — live probe
    showed it fabricating an inbox listing instead of handing off to the agent
    lane.
    """
    model = FakeVoiceModel()
    node = make_voice_node(model, tool_registry=_registry_with_all_tools())

    # A shortlist containing none of the voice tools (the real failing case).
    await node(_base_state(tools=[{"name": "gmail_read"}]), config={})

    assert set(model.bound_tool_names) == VOICE_TOOL_NAMES


@pytest.mark.asyncio
async def test_voice_tools_bound_when_shortlist_is_empty():
    model = FakeVoiceModel()
    node = make_voice_node(model, tool_registry=_registry_with_all_tools())

    await node(_base_state(tools=[]), config={})

    assert set(model.bound_tool_names) == VOICE_TOOL_NAMES


@pytest.mark.asyncio
async def test_voice_responder_no_bind_tools_when_registry_lacks_them():
    """If the registry cannot supply the voice tools at all, fall back to an
    unbound model rather than crashing — must still tag config for streaming."""
    model = FakeVoiceModel()
    registry = MagicMock()
    registry.get_langchain_tool_by_name.return_value = None
    node = make_voice_node(model, tool_registry=registry)

    await node(_base_state(tools=[{"name": "web_search"}]), config={})

    assert model.bound_tool_names is None
    assert "voice-llm" in model.last_config.get("tags", [])
