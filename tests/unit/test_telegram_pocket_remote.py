"""Unit tests for Telegram pocket-remote (inbound photos, telegram_agent lane)."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.orchestration.approval_gate import evaluate_approval_gate
from src.core.orchestration.context import turn_category_var
from src.core.orchestration.turn.mode_resolver import TurnModeResolver
from src.core.orchestration.turn.tool_selector import TurnToolSelector
from src.remote.telegram.artifact_delivery import (
    note_artifact_for_telegram,
    take_pending_artifacts,
)
from src.remote.telegram.inbound import DEFAULT_PHOTO_PROMPT, extract_inbound_content
from src.remote.telegram.telegram_agent import TELEGRAM_AGENT_TOOL_NAMES


def test_telegram_agent_category_survives_turn_modes():
    for mode in ("agent", "chat", "plan", "auto", ""):
        assert TurnModeResolver.category_for_turn("telegram_agent", mode) == "telegram_agent"


@pytest.mark.asyncio
async def test_mode_resolver_maps_telegram_agent_to_agent_lane():
    resolver = TurnModeResolver()
    turn_mode, source, _ = await resolver.resolve(
        prompt="check my email",
        category="telegram_agent",
        content_blocks=None,
        image_base64=None,
        history=[],
        initiator="user",
        platform="telegram",
    )
    assert turn_mode == "agent"
    assert source == "user_manual"


def test_telegram_agent_schemas_exclude_delegates():
    class FakeTool:
        def __init__(self, name: str):
            self.name = name

        def get_schema(self):
            return {"name": self.name, "description": self.name, "parameters": {}}

    registry = MagicMock()
    registry._tools = {
        name: FakeTool(name)
        for name in list(TELEGRAM_AGENT_TOOL_NAMES) + ["delegate_to_code_agent", "show_map"]
    }
    selection = TurnToolSelector(registry).telegram_agent_schemas()
    names = selection.selection["final_tool_names"]
    assert "web_search" in names
    assert "search_gmail" in names
    assert "generate_image" in names
    assert "render_chart" in names
    assert "render_animation" in names
    assert "set_reminder" in names
    assert "get_datetime" in names
    assert "delegate_to_code_agent" not in names
    assert "show_map" not in names
    assert selection.selection["path"] == "telegram_agent_fixed"


def test_extract_inbound_photo_builds_content_blocks():
    raw = b"fake-image-bytes"
    msg = {
        "caption": "what is this?",
        "photo": [
            {"file_id": "small", "width": 10},
            {"file_id": "large", "width": 800},
        ],
    }

    def download(file_id: str) -> bytes:
        assert file_id == "large"
        return raw

    prompt, blocks, has_media = extract_inbound_content(msg, download_file_bytes=download)
    assert prompt == "what is this?"
    assert has_media is True
    assert blocks is not None
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["data"] == base64.b64encode(raw).decode("ascii")


def test_extract_inbound_photo_without_caption_uses_default_prompt():
    msg = {"photo": [{"file_id": "only"}]}
    prompt, blocks, has_media = extract_inbound_content(
        msg, download_file_bytes=lambda _fid: b"x"
    )
    assert prompt == DEFAULT_PHOTO_PROMPT
    assert has_media is True
    assert blocks is not None


def test_extract_inbound_text_only_no_blocks():
    prompt, blocks, has_media = extract_inbound_content(
        {"text": "hi"}, download_file_bytes=lambda _fid: b""
    )
    assert prompt == "hi"
    assert blocks is None
    assert has_media is False


def test_artifact_queue_for_telegram_session():
    sid = "main:1:telegram:123"
    take_pending_artifacts(sid)  # clear
    note_artifact_for_telegram("/tmp/a.pdf", "a.pdf", session_id=sid)
    note_artifact_for_telegram("/tmp/a.pdf", "a.pdf", session_id=sid)  # dedupe
    pending = take_pending_artifacts(sid)
    assert len(pending) == 1
    assert pending[0]["filename"] == "a.pdf"
    assert take_pending_artifacts(sid) == []


def test_artifact_queue_ignores_non_telegram_session():
    take_pending_artifacts("gui-session")
    note_artifact_for_telegram("/tmp/a.pdf", "a.pdf", session_id="gui-session")
    assert take_pending_artifacts("gui-session") == []


def test_approval_gate_skips_on_telegram_agent(monkeypatch):
    token = turn_category_var.set("telegram_agent")
    try:
        outcome = evaluate_approval_gate("send_email")
        assert outcome.short_circuit is None
        assert outcome.decision == "approved"
    finally:
        turn_category_var.reset(token)


def test_contact_escalate_targets_telegram_agent_in_pipeline_source():
    """Sanity: escalate handoff category string is present in pipeline module."""
    src = Path("src/core/orchestration/turn/pipeline.py").read_text(encoding="utf-8")
    assert 'handoff_category = (' in src
    assert '"telegram_agent"' in src


def test_telegram_client_send_photo_local_file(tmp_path):
    from unittest.mock import MagicMock
    from src.remote.telegram.telegram_client import TelegramClient

    fake_img = tmp_path / "test.png"
    fake_img.write_bytes(b"\x89PNG\r\n\x1a\nfakeimagebytes")

    client = TelegramClient(bot_token="fake-token")
    mock_post = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 99}}
    mock_post.return_value = mock_resp
    client._client.post = mock_post

    res = client.send_photo(chat_id=12345, photo=str(fake_img), caption="here is your image")
    assert res["message_id"] == 99
    assert mock_post.call_count == 1
    call_args = mock_post.call_args
    assert "sendPhoto" in call_args[0][0]
    assert call_args[1]["data"]["chat_id"] == "12345"
    assert call_args[1]["data"]["caption"] == "here is your image"
    assert "photo" in call_args[1]["files"]


def test_telegram_turns_do_not_broadcast_to_gui():
    from src.shared.turn_delivery import (
        DELIVERY_TELEGRAM,
        resolve_delivery,
        is_gui_delivery,
        is_telegram_delivery,
    )
    from src.remote.ui_state_syncer import UIStateSyncer
    from src.application.events.event_bus import EventBus

    assert resolve_delivery("contact") == DELIVERY_TELEGRAM
    assert resolve_delivery("telegram_agent") == DELIVERY_TELEGRAM
    assert is_gui_delivery(DELIVERY_TELEGRAM) is False
    assert is_telegram_delivery(DELIVERY_TELEGRAM) is True

    syncer = UIStateSyncer(limb=MagicMock(), event_bus=EventBus())
    fake_event = MagicMock()
    fake_event.delivery = None
    fake_event.platform = "telegram"
    bucket = syncer._bucket_from_event(fake_event, session_id="main:42:telegram:100")
    assert bucket[1] == DELIVERY_TELEGRAM
    assert syncer._is_chat_turn(bucket) is False
    assert syncer._is_streamable_turn(bucket) is False


def test_gui_limb_ignores_telegram_messages():
    from src.remote.gui_limb import GUIChadLimb
    from src.remote.protocol import ChadMessage, MessageType

    limb = GUIChadLimb(MagicMock())
    tg_msg1 = ChadMessage(MessageType.AI_RESPONSE, {"text": "hello", "delivery": "telegram"})
    tg_msg2 = ChadMessage(MessageType.AI_RESPONSE, {"text": "hello", "category": "contact"})
    tg_msg3 = ChadMessage(MessageType.AI_RESPONSE, {"text": "hello", "conversation_id": "main:1:telegram:999"})
    gui_msg = ChadMessage(MessageType.AI_RESPONSE, {"text": "hello", "delivery": "gui", "conversation_id": "conv-123"})

    assert limb._is_telegram_delivery(tg_msg1) is True
    assert limb._is_telegram_delivery(tg_msg2) is True
    assert limb._is_telegram_delivery(tg_msg3) is True
    assert limb._is_telegram_delivery(gui_msg) is False


@pytest.mark.asyncio
async def test_autonomous_telegram_not_suppressed_by_connected_limb(monkeypatch):
    """Confirm autonomous alerts to Telegram are NOT suppressed just because a GUI limb is alive."""
    from src.remote.telegram.telegram_response_router import TelegramResponseRouter
    from src.remote.server import ChadServer
    from src.application.events.event_bus import EventBus
    from src.application.events.events import TurnCompleted
    from src.core.orchestration.conversation_orchestrator import TurnContext, TurnInitiator

    mock_server = MagicMock()
    mock_server.is_limb_connected.return_value = True
    monkeypatch.setattr(ChadServer, "instance", mock_server)

    router = TelegramResponseRouter(EventBus())
    mock_client = MagicMock()
    mock_client.send_message.return_value = {"ok": True, "result": {"message_id": 123}}
    monkeypatch.setattr(router, "_get_telegram_client", lambda: mock_client)

    turn_ctx = TurnContext(
        turn_id="turn-99",
        initiator=TurnInitiator.SYSTEM,
        category="system",
        user_input="",
        session_key="main:1:telegram:777888",
        platform="telegram",
        model_response="Upcoming event in 15 minutes: Team Meeting",
    )
    event = TurnCompleted(turn_id="turn-99", turn_context=turn_ctx, model_response=turn_ctx.model_response)

    await router._handle_turn_completed(event)
    assert mock_client.send_message.call_count == 1
    call_kwargs = mock_client.send_message.call_args[1]
    assert call_kwargs["chat_id"] == 777888
    assert "upcoming event in 15 minutes" in call_kwargs["text"]


