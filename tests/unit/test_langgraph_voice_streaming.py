r"""
Regression test for epic #291/#293 Phase 3: LangGraphEngine.invoke's
astream_events dispatch loop must forward 'voice-llm'-tagged chunks to
on_token, same as 'conversational-llm' — otherwise a tagging fix in
voice_node.py alone wouldn't be enough (see tests/unit/test_voice_node.py for
the node-side half of this regression).

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_langgraph_voice_streaming.py -q
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.orchestration.langgraph_engine import LangGraphEngine


class _Chunk:
    def __init__(self, content: str):
        self.content = content


def _fake_events(tag: str):
    async def _gen(*args, **kwargs):
        yield {
            "event": "on_chat_model_stream",
            "tags": [tag],
            "data": {"chunk": _Chunk("Let me check on that.")},
        }
        yield {
            "event": "on_chat_model_stream",
            "tags": ["some-internal-tag"],
            "data": {"chunk": _Chunk("internal reasoning, must not leak")},
        }
        yield {
            "event": "on_chain_end",
            "data": {"output": {"messages": [], "final_response": "Let me check on that."}},
        }
    return _gen


def _make_engine():
    adapter = MagicMock()
    adapter.model_name = "fake-model"
    engine = LangGraphEngine(adapter=adapter, tool_executor=MagicMock())
    return engine


@pytest.mark.asyncio
async def test_voice_llm_tagged_chunks_stream_to_on_token(monkeypatch):
    # Fast-tracks LangGraphEngine._get_checkpointer to MemorySaver (skips real
    # Postgres connection attempts). Scoped via monkeypatch (not a module-level
    # os.environ.setdefault) so it doesn't leak CHAD_TESTING into later tests.
    monkeypatch.setenv("CHAD_TESTING", "true")
    engine = _make_engine()
    fake_graph = MagicMock()
    fake_graph.astream_events = _fake_events("voice-llm")
    fake_graph.aget_state = AsyncMock(return_value=None)
    engine.get_graph = AsyncMock(return_value=fake_graph)

    received = []

    async def on_token(token):
        received.append(token)

    await engine.invoke({"messages": [], "on_token": on_token})

    assert received == ["Let me check on that."]


@pytest.mark.asyncio
async def test_untagged_internal_stream_is_suppressed(monkeypatch):
    """Sanity check that the fake harness actually distinguishes tags — an
    internal (subagent/planner) stream must never reach on_token."""
    monkeypatch.setenv("CHAD_TESTING", "true")
    engine = _make_engine()
    fake_graph = MagicMock()
    fake_graph.astream_events = _fake_events("planner-llm")
    fake_graph.aget_state = AsyncMock(return_value=None)
    engine.get_graph = AsyncMock(return_value=fake_graph)

    received = []

    async def on_token(token):
        received.append(token)

    await engine.invoke({"messages": [], "on_token": on_token})

    assert received == []
