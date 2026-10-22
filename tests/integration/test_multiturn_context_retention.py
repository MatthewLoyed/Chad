"""Integration coverage for issue #216: multi-turn conversation context retention.

Runs a real LangGraphEngine (real graph, real checkpointer) through 5+ turns on
the same thread_id and verifies each turn's history includes every prior turn's
messages, not just the current one. The LLM call itself is mocked (no live
LM Studio dependency needed for this to be a meaningful orchestration-layer
integration test); only the model backend is a test double.
"""
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

import pytest
from langchain_core.messages import HumanMessage

from src.core.orchestration.langgraph_engine import LangGraphEngine
from src.core.intelligence.adapters.ai_adapter_base import AIAdapter, GenerateResponse


class _TurnCountingAdapter(AIAdapter):
    """Echoes back how many prior turns it can see in the message history it is fed."""

    def __init__(self):
        self.seen_message_counts = []

    async def generate(self, messages, tools=None, **kwargs):
        self.seen_message_counts.append(len(messages))
        return GenerateResponse(text="ack TASK_COMPLETED", tool_calls=None, finish_reason="stop")

    def convert_tool_schemas(self, schemas):
        return []

    def build_tool_result_message(self, id, name, res):
        return {}

    def build_assistant_tool_call_message(self, calls, content=None):
        return {}

    async def is_ready(self):
        return True


def _turn_state(thread_id: str, text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "turn_id": thread_id,
        "session_id": thread_id,
        "category": "test",
        "platform": "pc",
        "is_simple": False,
        "active_plan": None,
        "plan_step": 1,
        "system_instruction": "You are a helpful assistant.",
        "tools": [],
        "total_tool_calls": 0,
        "tool_results": [],
        "max_tool_calls": 5,
        "final_response": None,
        "temperature": 0.7,
        "thinking_level": "medium",
        "on_token": None,
        "on_thinking": None,
        "variables": {},
    }


@pytest.mark.asyncio
async def test_context_preserved_across_five_plus_turns():
    os.environ["CHAD_TESTING"] = "true"
    from src.shared.config_manager import ConfigManager
    ConfigManager.get_config()["disabled_subagents"] = []
    from tests.unit.test_plan_execute import warmup_mock_registry
    warmup_mock_registry()

    adapter = _TurnCountingAdapter()
    engine = LangGraphEngine(adapter=adapter, tool_executor=None)
    thread_id = "multiturn-context-test"

    turn_texts = [f"turn number {i}" for i in range(1, 7)]  # 6 turns

    for text in turn_texts:
        await engine.invoke(_turn_state(thread_id, text), thread_id=thread_id)

    history = await engine.get_history(thread_id)
    contents = [m.content for m in history if hasattr(m, "content")]

    for text in turn_texts:
        assert any(text in c for c in contents), f"expected '{text}' still present in restored history"

    # The message count the model was fed on turn N must be >= turn N-1's count,
    # proving the checkpoint is accumulating context rather than resetting each turn.
    counts = adapter.seen_message_counts
    assert len(counts) == len(turn_texts)
    assert counts == sorted(counts), f"message count should be non-decreasing across turns, got {counts}"
    assert counts[-1] > counts[0], "last turn should see strictly more context than the first turn"
