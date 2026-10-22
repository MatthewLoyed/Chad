"""Unit tests for plan-approval response copy (no false "Task completed.")."""
from __future__ import annotations

from src.core.orchestration.turn.response_processor import TurnResponsePostProcessor


def test_awaiting_approval_empty_response_uses_review_prompt():
    outcome = TurnResponsePostProcessor.process(
        {"graph_interrupt_pending": True, "messages": []},
        metrics={},
        turn_start_time=0.0,
    )
    assert outcome.awaiting_approval is True
    assert outcome.stop_reason == "awaiting_approval"
    assert "Task completed" not in outcome.response
    assert "execution plan" in outcome.response.lower()


def test_awaiting_approval_replaces_task_completed_fallback():
    """Regression: early default used to win before interrupt detection."""
    outcome = TurnResponsePostProcessor.process(
        {
            "graph_interrupt_pending": True,
            "final_response": "Task completed.",
            "messages": [],
        },
        metrics={},
        turn_start_time=0.0,
    )
    assert outcome.awaiting_approval is True
    assert "Task completed" not in outcome.response
    assert "proceed" in outcome.response.lower()


def test_normal_empty_response_still_says_task_completed():
    outcome = TurnResponsePostProcessor.process(
        {"messages": []},
        metrics={},
        turn_start_time=0.0,
    )
    assert outcome.awaiting_approval is False
    assert outcome.response == "Task completed."


def test_timeout_error_response_not_awaiting_approval():
    """Regression: NodeTimeoutError set graph_interrupt_pending, causing timeouts to look like approval pauses."""
    outcome = TurnResponsePostProcessor.process(
        {
            "graph_interrupt_pending": True,
            "final_response": "⚠️ This step exceeded the maximum allowed time (900s). Try breaking the request into smaller tasks.",
            "messages": [],
        },
        metrics={},
        turn_start_time=0.0,
    )
    assert outcome.awaiting_approval is False
    assert outcome.stop_reason == "error"
    assert "exceeded the maximum allowed time" in outcome.response
