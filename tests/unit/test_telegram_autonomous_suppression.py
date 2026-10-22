"""Unit tests for Telegram autonomous response suppression, prefix cleaning, and deduplication."""

import pytest
from src.remote.telegram.telegram_response_router import (
    _is_silent_heartbeat_response,
    _clean_conversational_prefix,
    TelegramResponseRouter,
)
from src.application.events.event_bus import EventBus


def test_is_silent_heartbeat_response():
    assert _is_silent_heartbeat_response("HEARTBEAT_OK") is True
    assert _is_silent_heartbeat_response("heartbeat_ok") is True
    assert _is_silent_heartbeat_response("HEARTBEAT_OK - nothing to report") is True
    assert _is_silent_heartbeat_response("Since you're probably in the middle of data science right now I'll stay quiet until something important hits") is True
    assert _is_silent_heartbeat_response("quick heartbeat scan nothing urgent in your inbox right now") is True
    assert _is_silent_heartbeat_response("Inbox is clear, all quiet right now.") is True
    assert _is_silent_heartbeat_response(None) is True
    assert _is_silent_heartbeat_response("") is True

    # Real alerts should NOT be suppressed
    assert _is_silent_heartbeat_response("heads up, your professor moved the deadline to Friday!") is False
    assert _is_silent_heartbeat_response("you have a meeting with Sarah starting in 15 minutes") is False

    # Expanded 2026 suppression phrases
    assert _is_silent_heartbeat_response("gmail sync event came back empty") is True
    assert _is_silent_heartbeat_response("history id had no new messages") is True
    assert _is_silent_heartbeat_response("just a spam bot blast") is True
    assert _is_silent_heartbeat_response("routine notification, nothing actionable") is True


def test_format_telegram_prose_lowercase():
    from src.remote.telegram.telegram_response_router import format_telegram_prose_lowercase

    assert format_telegram_prose_lowercase("Hey Matthew! How was the workout?") == "hey matthew! how was the workout?"
    assert format_telegram_prose_lowercase("Yo Check This Repo: https://GitHub.com/Owner/Repo!") == "yo check this repo: https://GitHub.com/Owner/Repo!"
    assert format_telegram_prose_lowercase("<b>HELLO</b> <a href=\"https://Example.com/Path\">Click Here</a>") == "<b>hello</b> <a href=\"https://Example.com/Path\">click here</a>"
    assert format_telegram_prose_lowercase("run <code>git commit -m \"Fix Bug\"</code> now") == "run <code>git commit -m \"Fix Bug\"</code> now"
    assert format_telegram_prose_lowercase("") == ""


def test_clean_conversational_prefix():
    assert _clean_conversational_prefix("Hey Matthew! How was the workout?") == "How was the workout?"
    assert _clean_conversational_prefix("Hey Matthew, how was the workout?") == "how was the workout?"
    assert _clean_conversational_prefix("Hello Matthew: check this out") == "check this out"
    assert _clean_conversational_prefix("Matthew, got that done for you.") == "got that done for you."
    assert _clean_conversational_prefix("yo what's up") == "yo what's up"
    assert _clean_conversational_prefix("how was the gym today?") == "how was the gym today?"


def test_duplicate_autonomous_alert_dedup():
    router = TelegramResponseRouter(EventBus())
    chat_id = 12345

    msg = "quick reminder: your class Data Science 401 starts at 2:00 PM."
    # First time should not be duplicate
    assert router._is_duplicate_autonomous_alert(chat_id, msg) is False
    # Second time within TTL should be suppressed as duplicate
    assert router._is_duplicate_autonomous_alert(chat_id, msg) is True
    # Slight variation / substring should also be duplicate
    variant = "Quick reminder: your class Data Science 401 starts at 2:00 PM!"
    assert router._is_duplicate_autonomous_alert(chat_id, variant) is True

    # Different message should pass
    diff_msg = "you got an urgent email from Professor Jones."
    assert router._is_duplicate_autonomous_alert(chat_id, diff_msg) is False


@pytest.mark.asyncio
async def test_stay_silent_tool_suppression():
    from unittest.mock import AsyncMock, patch, MagicMock
    from src.application.events.events import TurnCompleted
    from src.core.orchestration.conversation_orchestrator import TurnContext, TurnInitiator

    router = TelegramResponseRouter(EventBus())
    router.send_text = AsyncMock()

    # Mock TurnContext where stay_silent was executed
    ctx = TurnContext(
        turn_id="turn-123",
        initiator=TurnInitiator.WEBHOOK,
        category="autonomous",
        session_key="telegram:bot:channel:12345",
        platform="telegram",
        model_response="here is a summary of some emails",
        tools_executed=["get_gmail_history", "stay_silent"],
    )

    event = TurnCompleted(turn_id="turn-123", turn_context=ctx)
    await router._handle_turn_completed(event)

    # Must be completely suppressed
    router.send_text.assert_not_called()

