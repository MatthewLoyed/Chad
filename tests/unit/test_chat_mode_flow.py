"""Chat mode must skip the 9B tool selector end-to-end."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.orchestration.turn.mode_resolver import TurnModeResolver
from src.core.orchestration.turn.tool_selector import TurnToolSelector
from src.core.orchestration.turn_coordinator import TurnCoordinator
from src.remote.limb_message_handler import LimbMessageHandler
from src.remote.protocol import ChadMessage, MessageType
from src.shared.context import RequestContext


@pytest.mark.asyncio
async def test_mode_resolver_chat_wins_over_attachments():
    resolver = TurnModeResolver()
    mode, source, _ = await resolver.resolve(
        prompt="summarize this pdf",
        category="chat",
        content_blocks=[{"type": "document", "name": "x.pdf"}],
        image_base64=None,
        history=[],
        initiator="user",
        platform="gui",
    )
    assert mode == "chat"
    assert source == "user_manual"


@pytest.mark.asyncio
async def test_mode_resolver_eval_honors_explicit_planning_category():
    """platform=eval must skip the intent router but NOT demote category=planning → agent.

    HITL seed 2026-07-24: wedding_quality_hitl sent category=planning + platform=eval and
    got 0 plan steps because resolve() always returned agent for eval platforms.
    """
    resolver = TurnModeResolver()
    mode, source, _ = await resolver.resolve(
        prompt="Find my wedding roadmap and brief me",
        category="planning",
        content_blocks=None,
        image_base64=None,
        history=[],
        initiator="user",
        platform="eval",
    )
    assert mode == "plan"
    assert source == "system_turn_explicit"


@pytest.mark.asyncio
async def test_mode_resolver_eval_without_explicit_category_stays_agent():
    resolver = TurnModeResolver()
    mode, source, _ = await resolver.resolve(
        prompt="hello",
        category="auto",
        content_blocks=None,
        image_base64=None,
        history=[],
        initiator="user",
        platform="eval",
    )
    assert mode == "agent"
    assert source == "system_turn"


@pytest.mark.asyncio
async def test_mode_resolver_auto_with_attachments_still_agent():
    resolver = TurnModeResolver()
    with patch.object(resolver, "get_intent_router", return_value=None):
        mode, source, _ = await resolver.resolve(
            prompt="summarize this",
            category="auto",
            content_blocks=[{"type": "document", "name": "x.pdf"}],
            image_base64=None,
            history=[],
            initiator="user",
            platform="gui",
        )
    assert mode == "agent"
    assert source == "multimodal_override"


@pytest.mark.asyncio
async def test_turn_coordinator_chat_skips_tool_selector():
    tc = TurnCoordinator(tool_registry=MagicMock())
    tc.mcp_ready_event = None
    tc._history_loader.load = AsyncMock(return_value=[])
    tc._get_optimized_tools = AsyncMock(side_effect=AssertionError("9B path must not run in chat mode"))
    tc._consolidator.preflight = AsyncMock(side_effect=lambda *a, **k: a[3] if len(a) > 3 else [])
    tc._preload_main_model = AsyncMock()
    tc._mode_resolver = TurnModeResolver()

    adapter = MagicMock()
    adapter.model_name = "google/gemma-4-26b-a4b-qat"

    mock_engine = MagicMock()
    mock_engine.adapter = adapter
    mock_engine.get_history = AsyncMock(return_value=[])
    mock_engine.invoke = AsyncMock(return_value={"final_response": "ok"})
    tc._langgraph_engine = mock_engine

    ctx = RequestContext(platform="gui", session_id="main:default:gui:conv-1", category="chat", turn_id="turn-1")

    with patch("src.core.orchestration.turn.state_builder.SystemPromptBuilder.build", return_value="sys"):
        with patch.object(tc, "_get_chat_adapter", return_value=None):
            result = await tc._execute_turn_internal(
                "hey there",
                ctx,
                adapter=adapter,
                category="chat",
                initiator="user",
            )

    assert result == "ok"
    tc._get_optimized_tools.assert_not_called()
    assert tc._current_turn_mode == "chat"
    assert tc._last_tool_selection.get("path") == "turn_mode_chat"


@pytest.mark.asyncio
async def test_tool_selector_chat_lane_no_registry_call():
    registry = MagicMock()
    registry.get_schemas_for_turn = MagicMock(side_effect=AssertionError("registry selector must not run"))
    sel = TurnToolSelector(registry)
    out = await sel.select("hello", category="chat")
    assert len(out.schemas) == len(TurnToolSelector._CHAT_TOOL_NAMES)
    assert out.selection.get("path") == "turn_mode_chat"
    registry.get_schemas_for_turn.assert_not_called()


def test_registry_chat_lane_skips_llm_selector():
    from src.infrastructure.tools.registry import ToolRegistry
    from src.infrastructure.tools.selection.turn_selector import (
        TurnToolSelector as RegistryTurnToolSelector,
    )

    registry = ToolRegistry.__new__(ToolRegistry)
    registry._core_tool_names = []
    registry._tools = {}
    registry._llm_selector = MagicMock()
    registry._llm_selector.is_available = MagicMock(return_value=True)
    registry._llm_selector.select = MagicMock(side_effect=AssertionError("9B must not run for chat category"))
    registry._turn_selector = RegistryTurnToolSelector(registry)

    result = registry.get_schemas_for_turn("hello", category="chat")
    assert result["schemas"] == []
    assert result["selection"]["path"] == "turn_mode_chat"
    registry._llm_selector.select.assert_not_called()


def test_limb_coordinator_set_ui_turn_mode_schedules_chat_preload():
    handler = LimbMessageHandler.__new__(LimbMessageHandler)
    handler._limb = MagicMock(turn_coordinator=MagicMock())
    with patch("src.core.intelligence.model_lifecycle.schedule_warm") as mock_warm:
        with patch("src.infrastructure.repositories.config_repository.ConfigRepository") as mock_repo:
            mock_repo.return_value.update_preference = MagicMock()
            with patch("src.shared.config_manager.ConfigManager._recursive_update"):
                handler._handle_ui_turn_mode(
                    ChadMessage(MessageType.SET_UI_TURN_MODE, {"ui_mode": "chat"})
                )
    mock_warm.assert_called_once()
    assert mock_warm.call_args.kwargs.get("ui_mode") == "chat"


def test_user_input_category_chat_reaches_mode_resolver():
    """USER_INPUT payload category=chat is the wire contract from ChatInput."""
    msg = ChadMessage(
        MessageType.USER_INPUT,
        {"text": "hi", "category": "chat", "delivery": "gui", "turn_id": "t1"},
    )
    assert msg.payload.get("category") == "chat"
