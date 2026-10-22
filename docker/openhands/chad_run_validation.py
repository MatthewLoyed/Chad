"""Validate SDK termination without importing the SDK or judging task correctness."""
import re


class IncompleteRunError(RuntimeError):
    """SDK termination does not establish even a completed conversation."""


def validate_conversation(conversation):
    state = conversation.state
    status = getattr(state.execution_status, "value", state.execution_status)
    if status != "finished":
        raise IncompleteRunError(f"conversation_incomplete: execution_status={status}")

    # Look at the last agent event, not the transcript (which includes user code,
    # earlier failed attempts and quoted tool syntax). A finish action is valid.
    last = next((event for event in reversed(list(state.events))
                 if getattr(event, "source", None) == "agent"), None)
    if last is None:
        raise IncompleteRunError("conversation_incomplete: no final agent event")
    message = getattr(last, "llm_message", None)
    if message is None:
        return
    content = message.content
    text = content if isinstance(content, str) else "\n".join(
        getattr(block, "text", "") for block in content)
    if not text.strip() and not getattr(message, "tool_calls", None):
        raise IncompleteRunError("conversation_incomplete: empty final agent message")
    if getattr(message, "tool_calls", None) or re.match(
        r"\s*(?:<\|tool_call_start\|>|<function[= >]|<tool_call>)", text
    ):
        raise IncompleteRunError("unexecuted_tool_call: final agent message contains a tool request")
