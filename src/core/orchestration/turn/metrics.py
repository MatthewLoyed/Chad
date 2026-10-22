from __future__ import annotations

import re
from typing import Any, Dict, Optional


def blank_metrics() -> Dict[str, Any]:
    """Fresh per-turn metrics scaffold, populated as the turn progresses."""
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": None,
        "context_window": None,
        "ttft": None,
        "stop_reason": "task_completed",
        "speed": 0.0,
        "duration": 0.0,
        "estimated_prompt_tokens": None,
    }


def classify_stop_reason(
    final_response: Optional[str], final_state: Optional[Dict[str, Any]]
) -> str:
    """Derive the canonical stop reason for a completed turn.

    Order matters: an explicit pause (awaiting approval/input) overrides the
    text/length heuristics so clients can tell "waiting on the user" apart from
    "done" without parsing the response body.
    """
    from langchain_core.messages import AIMessage

    final_state = final_state or {}
    stop_reason = "task_completed"

    # Check if length truncation happened on the last non-tool-call AIMessage
    messages = final_state.get("messages", [])
    last_ai_msg = None
    if messages:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                if getattr(msg, "tool_calls", None):
                    continue
                last_ai_msg = msg
                break

    if last_ai_msg and last_ai_msg.additional_kwargs.get("finish_reason") == "length":
        stop_reason = "length"
    elif last_ai_msg and last_ai_msg.additional_kwargs.get("finish_reason") == "error":
        # The adapter (litellm_adapter._generate_impl) sets finish_reason="error" on any
        # generation failure (context-window overflow, provider disconnect, etc.) -- this was
        # silently dropped here before, so a real failure surfaced as an empty final_response
        # that response_processor.py then defaulted to a cheerful "Task completed." with no
        # trace of the underlying error anywhere (harness or real user session).
        stop_reason = "error"
    elif final_response and re.search(r"\bNEEDS_HUMAN\b", final_response, re.IGNORECASE):
        stop_reason = "human_needed"
    elif final_response and (
        str(final_response).startswith("⚠️")
        or re.search(r"\bTASK_FAILED\b", final_response, re.IGNORECASE)
    ):
        stop_reason = "error"
    elif final_response and (
        re.search(r"\bTASK_COMPLETED\b", final_response, re.IGNORECASE)
        or "[[done]]" in final_response
    ):
        stop_reason = "task_completed"

    # A paused turn (plan awaiting approval, or mid-execution pause_for_input)
    # otherwise looks identical to a completion; surface the pause explicitly.
    # Errors (e.g. ⚠️ timeout) are terminal and must never be overridden to awaiting_approval.
    if stop_reason != "error" and not str(final_response).startswith("⚠️"):
        if final_state.get("graph_interrupt_pending"):
            stop_reason = "awaiting_approval"
        elif final_state.get("paused_for_input"):
            stop_reason = "awaiting_input"
        elif final_state.get("pending_approval"):
            stop_reason = "awaiting_input"

    return stop_reason
