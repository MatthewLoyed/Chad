"""TurnCoordinator wiring for the semantic response cache (#516).

Verifies the cache actually short-circuits a real (mocked) turn end-to-end:
a side-effect-free turn (zero tool calls) gets stored, a near-repeat of the
same query on the same session is served from cache without touching the
LangGraph engine, and a turn that ran a tool is never cached.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.orchestration.turn.mode_resolver import TurnModeResolver
from src.core.orchestration.turn_coordinator import TurnCoordinator
from src.core.intelligence.semantic_cache import SemanticResponseCache
from src.shared.context import RequestContext


class _FixedEmbeddingProvider:
    """Same embedding for any text so lookups are deterministic in tests."""

    def get_embedding(self, text):
        return [1.0, 0.0, 0.0]


def _make_coordinator(final_response: str, total_tool_calls: int = 0) -> TurnCoordinator:
    tc = TurnCoordinator(tool_registry=MagicMock())
    tc.mcp_ready_event = None
    tc._history_loader.load = AsyncMock(return_value=[])
    tc._get_optimized_tools = AsyncMock(return_value=[])
    tc._consolidator.preflight = AsyncMock(side_effect=lambda *a, **k: a[3] if len(a) > 3 else [])
    tc._preload_main_model = AsyncMock()
    tc._mode_resolver = TurnModeResolver()
    tc._semantic_cache = SemanticResponseCache(
        embedding_provider=_FixedEmbeddingProvider(), similarity_threshold=0.95, ttl_seconds=300
    )

    adapter = MagicMock()
    adapter.model_name = "google/gemma-4-26b-a4b-qat"

    mock_engine = MagicMock()
    mock_engine.adapter = adapter
    mock_engine.get_history = AsyncMock(return_value=[])
    mock_engine.invoke = AsyncMock(
        return_value={"final_response": final_response, "total_tool_calls": total_tool_calls}
    )
    tc._langgraph_engine = mock_engine
    tc._adapter = adapter
    return tc


async def _run_turn(tc: TurnCoordinator, adapter, prompt: str, session_id: str, turn_id: str) -> str:
    ctx = RequestContext(platform="gui", session_id=session_id, category="agent", turn_id=turn_id)
    with patch("src.core.orchestration.turn.state_builder.SystemPromptBuilder.build", return_value="sys"):
        return await tc._execute_turn_internal(
            prompt, ctx, adapter=adapter, category="agent", initiator="user",
        )


@pytest.mark.asyncio
async def test_side_effect_free_turn_is_cached_and_served_on_repeat():
    tc = _make_coordinator("The sky is blue.", total_tool_calls=0)
    adapter = tc._langgraph_engine.adapter
    session_id = "main:default:gui:conv-cache-1"

    first = await _run_turn(tc, adapter, "why is the sky blue", session_id, "turn-1")
    assert first == "The sky is blue."
    assert tc._langgraph_engine.invoke.await_count == 1

    # Near-repeat: engine must NOT be invoked again — served straight from cache.
    second = await _run_turn(tc, adapter, "why is the sky blue?", session_id, "turn-2")
    assert second == "The sky is blue."
    assert tc._langgraph_engine.invoke.await_count == 1


@pytest.mark.asyncio
async def test_turn_with_tool_calls_is_never_cached():
    tc = _make_coordinator("Sent the email.", total_tool_calls=1)
    adapter = tc._langgraph_engine.adapter
    session_id = "main:default:gui:conv-cache-2"

    await _run_turn(tc, adapter, "send that email", session_id, "turn-1")
    await _run_turn(tc, adapter, "send that email", session_id, "turn-2")

    # A turn that executed a tool must never be served from cache — real-world
    # effects (or now-stale results) can't be safely replayed.
    assert tc._langgraph_engine.invoke.await_count == 2


@pytest.mark.asyncio
async def test_cache_is_scoped_per_session():
    tc = _make_coordinator("The sky is blue.", total_tool_calls=0)
    adapter = tc._langgraph_engine.adapter

    await _run_turn(tc, adapter, "why is the sky blue", "session-a", "turn-1")
    await _run_turn(tc, adapter, "why is the sky blue", "session-b", "turn-2")

    # Different sessions never share a cached answer.
    assert tc._langgraph_engine.invoke.await_count == 2
