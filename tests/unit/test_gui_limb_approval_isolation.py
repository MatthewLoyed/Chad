"""
Session-isolation invariants for the desktop Limb's approval routing (issue #269).

The clarifying-question / tool-approval card belongs to exactly ONE conversation.
GUIChadLimb.handle_message must:
  * drop a TOOL_APPROVAL_REQUEST whose conversation_id != the in-flight chat, and
  * stamp camelCase `conversationId` onto the payload it forwards to the React
    store (whose guard reads that key) when the ids match.

These run without a live Brain/webview — we build the limb via __new__ and stub
only what the approval branch touches, so a regression fails here instantly.
"""

import asyncio
from unittest.mock import MagicMock

from src.remote.gui_limb import GUIChadLimb
from src.remote.protocol import ChadMessage, MessageType


def _make_limb(pending_conversation_id):
    """A GUIChadLimb with only the state the approval branch needs (no real init)."""
    limb = GUIChadLimb.__new__(GUIChadLimb)
    limb.window = MagicMock()
    limb.pending_conversation_id = pending_conversation_id
    return limb


def _approval_msg(conversation_id):
    return ChadMessage(
        MessageType.TOOL_APPROVAL_REQUEST,
        {
            "token": "tok-1",
            "tool_name": "ask_user_question",
            "decision_type": "questions",
            "prompt": "Which approach?",
            "conversation_id": conversation_id,
        },
    )


def test_matching_conversation_forwards_with_camelcase_id():
    limb = _make_limb(pending_conversation_id="conv-a")

    asyncio.run(limb.handle_message(_approval_msg("conv-a")))

    limb.window.push_tool_approval_request.assert_called_once()
    payload = limb.window.push_tool_approval_request.call_args.args[0]
    # The React store guard reads camelCase `conversationId`.
    assert payload["conversationId"] == "conv-a"


def test_mismatched_conversation_is_dropped():
    limb = _make_limb(pending_conversation_id="conv-a")

    # Card raised for conv-b while conv-a is the in-flight chat → must NOT surface.
    asyncio.run(limb.handle_message(_approval_msg("conv-b")))

    limb.window.push_tool_approval_request.assert_not_called()


def test_untagged_approval_still_forwards_legacy_path():
    # No conversation_id (legacy emitter) → forwarded so old flows don't regress.
    limb = _make_limb(pending_conversation_id="conv-a")

    asyncio.run(limb.handle_message(_approval_msg(None)))

    limb.window.push_tool_approval_request.assert_called_once()
    payload = limb.window.push_tool_approval_request.call_args.args[0]
    assert "conversationId" not in payload


def test_late_paused_turn_response_preserves_approval_continuation_stream():
    limb = _make_limb("conv-a")
    limb.current_turn_id = "approval-continuation"
    emitter = object()
    limb._stream_emitter = emitter
    limb._turn_thinking_events = [{"text": "new thoughts"}]
    limb._is_voice_delivery = lambda msg: False
    asyncio.run(limb.handle_message(ChadMessage(MessageType.AI_RESPONSE, {
        "turn_id": "paused-turn", "conversation_id": "conv-a", "text": "Awaiting approval",
    })))
    assert limb._stream_emitter is emitter
    assert limb._turn_thinking_events == [{"text": "new thoughts"}]
    limb.window.update_status.assert_not_called()
