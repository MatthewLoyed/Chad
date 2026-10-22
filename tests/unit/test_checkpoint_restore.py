"""Checkpoint restore round-trip coverage for issue #216.

test_langgraph_engine.py::test_safe_serializer_checkpointer only exercises
the save (aput) side of SafeMemorySaver. These tests cover the restore side:
a checkpoint written via aput can be read back via aget_tuple with the same
channel_values, and a live LangGraphEngine restores prior turn state
(messages, session_id) from a checkpoint on a second invoke with the same
thread_id.
"""
import os
import pytest
from langchain_core.messages import HumanMessage, AIMessage

from src.core.orchestration.langgraph_engine import LangGraphEngine, SafeMemorySaver
from src.core.intelligence.adapters.ai_adapter_base import AIAdapter, GenerateResponse


@pytest.mark.asyncio
async def test_safe_memory_saver_round_trip():
    """A checkpoint written via aput is readable back via aget_tuple with the same data."""
    checkpointer = SafeMemorySaver()
    config = {"configurable": {"thread_id": "restore-test", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1,
        "id": "checkpoint-1",
        "ts": "2026-07-18T00:00:00+00:00",
        "channel_values": {
            "messages": [{"content": "hello"}, {"content": "world"}],
            "session_id": "restore-test",
        },
        "channel_versions": {"messages": "1", "session_id": "1"},
        "versions_seen": {},
    }
    new_versions = {"messages": "1", "session_id": "1"}
    await checkpointer.aput(config, checkpoint, {}, new_versions)

    restored = await checkpointer.aget_tuple(config)
    assert restored is not None
    assert restored.checkpoint["channel_values"]["session_id"] == "restore-test"
    assert restored.checkpoint["channel_values"]["messages"] == [
        {"content": "hello"},
        {"content": "world"},
    ]


class _EchoAdapter(AIAdapter):
    async def generate(self, messages, tools=None, **kwargs):
        return GenerateResponse(text="ack TASK_COMPLETED", tool_calls=None, finish_reason="stop")

    def convert_tool_schemas(self, schemas):
        return []

    def build_tool_result_message(self, id, name, res):
        return {}

    def build_assistant_tool_call_message(self, calls, content=None):
        return {}

    async def is_ready(self):
        return True


def _initial_state(thread_id: str, text: str) -> dict:
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
async def test_engine_restores_prior_turn_messages_from_checkpoint(monkeypatch):
    """Second invoke on the same thread_id sees the first turn's messages via the checkpointer."""
    # monkeypatch (not a raw os.environ assignment) so CHAD_TESTING doesn't
    # leak "true" into later tests in the session.
    monkeypatch.setenv("CHAD_TESTING", "true")
    from src.shared.config_manager import ConfigManager
    ConfigManager.get_config()["disabled_subagents"] = []
    from test_plan_execute import warmup_mock_registry
    warmup_mock_registry()

    engine = LangGraphEngine(adapter=_EchoAdapter(), tool_executor=None)
    thread_id = "restore-engine-test"

    await engine.invoke(_initial_state(thread_id, "first turn"), thread_id=thread_id)
    history_after_first = await engine.get_history(thread_id)
    assert any("first turn" in (m.content or "") for m in history_after_first)

    await engine.invoke(_initial_state(thread_id, "second turn"), thread_id=thread_id)
    history_after_second = await engine.get_history(thread_id)

    # The checkpoint must have accumulated across turns, not reset each invoke.
    assert len(history_after_second) > len(history_after_first)
    contents = [m.content for m in history_after_second if hasattr(m, "content")]
    assert any("first turn" in c for c in contents)
    assert any("second turn" in c for c in contents)
