"""
Unit tests for the Telegram 9B contact front desk.

Covers mode/category survival, graph entry, lean peer context, escalate detection,
and contact-node tool binding (mirrors voice-lane coverage).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable

from src.core.orchestration.nodes.contact_node import CONTACT_TOOL_NAMES, make_contact_node
from src.core.orchestration.nodes.routing import route_after_contact_responder, route_entry
from src.core.orchestration.turn.mode_resolver import TurnModeResolver
from src.core.orchestration.turn.response_processor import TurnResponsePostProcessor
from src.core.orchestration.turn.state_builder import TurnStateBuilder
from src.remote.telegram.contact_context import build_contact_peer_block


def test_contact_category_survives_agent_turn_mode():
    assert TurnModeResolver.category_for_turn("contact", "agent") == "contact"


@pytest.mark.parametrize("turn_mode", ["agent", "chat", "plan", "auto", ""])
def test_contact_category_survives_every_turn_mode(turn_mode):
    assert TurnModeResolver.category_for_turn("contact", turn_mode) == "contact"


@pytest.mark.asyncio
async def test_mode_resolver_maps_contact_to_chat_lane():
    resolver = TurnModeResolver()
    turn_mode, source, _ = await resolver.resolve(
        prompt="heyyy",
        category="contact",
        content_blocks=None,
        image_base64=None,
        history=[],
        initiator="user",
        platform="telegram",
    )
    assert turn_mode == "chat"
    assert source == "user_manual"


def test_route_entry_sends_contact_to_contact_responder():
    state = {"category": "contact", "turn_mode": "chat", "messages": []}
    assert route_entry(state) == "contact_responder"


def test_route_after_contact_goes_to_tools_when_escalate_called():
    state = {
        "category": "contact",
        "messages": [
            AIMessage(
                content="On it Matthew",
                tool_calls=[
                    {
                        "id": "1",
                        "name": "escalate_to_agent",
                        "args": {"reason": "email"},
                    }
                ],
            )
        ],
    }
    assert route_after_contact_responder(state) == "tools"


def test_route_after_tools_ends_contact_on_escalate():
    from src.core.orchestration.nodes.routing import route_after_tools

    state = {
        "category": "contact",
        "voice_escalate": True,
        "messages": [AIMessage(content="On it")],
    }
    assert route_after_tools(state) == "__end__"


def test_route_after_contact_ends_without_tools():
    state = {
        "category": "contact",
        "messages": [AIMessage(content="heyyy wsp Matthew")],
    }
    assert route_after_contact_responder(state) == "__end__"


class _Telemetry:
    on_token = None
    on_thinking = None
    on_usage = None


def test_contact_state_caps_tools_and_routes():
    category = TurnModeResolver.category_for_turn("contact", "chat")
    state = TurnStateBuilder.build(
        lc_messages=[],
        turn_id="t1",
        session_id="main:8626369319:telegram:8626369319",
        category=category,
        turn_mode="chat",
        platform="telegram",
        is_simple=True,
        sys_instr="",
        tools=[],
        max_tool_calls=12,
        thinking_level="minimal",
        bundled_skill_content=None,
        bundled_skill_name=None,
        telemetry=_Telemetry(),
        metadata=None,
    )
    assert state["category"] == "contact"
    assert state["max_tool_calls"] == 4
    assert route_entry(state) == "contact_responder"


def test_build_system_instruction_includes_peer_name(monkeypatch):
    fake_chat = SimpleNamespace(
        first_name="Matthew",
        last_name="",
        username="matt",
        chat_id=8626369319,
    )

    class FakeRepo:
        def get_chat_id(self, session_key):
            return 8626369319

        def get_chat(self, chat_id):
            return fake_chat

    monkeypatch.setattr(
        "src.infrastructure.repositories.telegram_chat_repository.TelegramChatRepository",
        FakeRepo,
    )
    block = build_contact_peer_block(session_key="main:8626369319:telegram:8626369319")
    assert "Matthew" in block
    assert "TELEGRAM CONTACT" in block

    monkeypatch.setattr(
        "src.core.intelligence.prompt_builder.SystemPromptBuilder.build",
        staticmethod(lambda **kwargs: "PERSONA"),
    )
    instruction = TurnStateBuilder.build_system_instruction(
        category="contact",
        turn_mode="chat",
        initiator="user",
        platform="telegram",
        prompt="heyyy",
        history=[],
        memory_engine=None,
        session_id="main:8626369319:telegram:8626369319",
    )
    assert instruction.startswith("PERSONA")
    assert "Matthew" in instruction


def test_greeting_does_not_flag_escalate():
    final_state = {
        "messages": [AIMessage(content="heyyy wsp Matthew")],
        "voice_escalate": None,
    }
    escalate, reason = TurnResponsePostProcessor.check_voice_escalate(final_state, "contact")
    assert escalate is False
    assert reason is None


def test_task_escalate_flagged_for_contact():
    final_state = {
        "messages": [AIMessage(content="On it Matthew — checking your email")],
        "voice_escalate": True,
        "voice_escalate_reason": "check email for lease",
    }
    escalate, reason = TurnResponsePostProcessor.check_voice_escalate(final_state, "contact")
    assert escalate is True
    assert "lease" in (reason or "")


def test_extract_front_desk_ack_prefers_ai_text():
    final_state = {
        "messages": [
            AIMessage(content="On it Matthew looking at email"),
        ],
        "final_response": "ignored",
    }
    assert "Matthew" in TurnResponsePostProcessor.extract_front_desk_ack(final_state)


def test_voice_escalate_still_works_only_for_voice_flag():
    final_state = {"voice_escalate": True, "voice_escalate_reason": "r"}
    assert TurnResponsePostProcessor.check_voice_escalate(final_state, "voice")[0] is True
    assert TurnResponsePostProcessor.check_voice_escalate(final_state, "agent")[0] is False


class FakeContactModel(Runnable):
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


@pytest.mark.asyncio
async def test_contact_responder_tags_and_binds_escalate_only():
    model = FakeContactModel()

    def get_tool(name):
        tool = MagicMock()
        tool.name = name
        tool.ainvoke = AsyncMock(return_value={})
        return tool

    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = get_tool
    node = make_contact_node(model, tool_registry=registry)

    await node(
        {
            "messages": [HumanMessage(content="check my email")],
            "system_instruction": "You are Chad.",
            "iteration_count": 0,
        },
        config={},
    )

    assert "contact-llm" in model.last_config.get("tags", [])
    assert model.bound_tool_names == ["escalate_to_agent"]
    assert CONTACT_TOOL_NAMES == frozenset({"escalate_to_agent"})
