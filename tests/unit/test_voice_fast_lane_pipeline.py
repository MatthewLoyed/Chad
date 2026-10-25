r"""
Unit tests for the pipeline-level voice fast-lane short circuit (issue #654
problem 3 follow-up).

Live testing showed the fast lane wired only at the voice_node/graph level still
waited on the pipeline's unconditional `_preload_main_model` (JIT-loading the 9B
voice model into LM Studio) before ever reaching the node — explaining both the
"tool doesn't open instantly" complaint and the LM Studio OOM errors. These tests
prove `TurnPipeline._try_voice_fast_lane` short-circuits BEFORE mode resolution,
tool selection, and model preload for a fast-lane hit.

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_fast_lane_pipeline.py -q
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.orchestration.turn_coordinator import TurnCoordinator
from src.shared.context import RequestContext


@pytest.fixture(autouse=True)
def _mock_quick_tool_model(monkeypatch):
    monkeypatch.setattr(
        "src.core.orchestration.nodes.voice_quick_tool.try_quick_tool_call",
        AsyncMock(return_value=None),
    )


def _registry_with(tool_name: str):
    tool = MagicMock()
    tool.name = tool_name
    tool.ainvoke = AsyncMock(return_value={})

    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = (
        lambda name: tool if name == tool_name else None
    )
    return registry, tool


def _build_coordinator(tool_registry):
    coordinator = TurnCoordinator(tool_registry=tool_registry, event_bus=MagicMock())
    coordinator.default_adapter = MagicMock()
    coordinator.default_adapter.model_name = "test-model"
    # Any call into these means the fast lane failed to short-circuit.
    coordinator._preload_main_model = AsyncMock(
        side_effect=AssertionError("model preload must not run on a fast-lane hit")
    )
    coordinator._history_loader.load = AsyncMock(
        side_effect=AssertionError("history load must not run on a fast-lane hit")
    )
    return coordinator


@pytest.mark.asyncio
async def test_fast_lane_hit_skips_preload_and_history_load():
    registry, tool = _registry_with("open_browser")
    coordinator = _build_coordinator(registry)
    context = RequestContext(platform="voice", session_id="s1")

    response = await coordinator._execute_turn_internal(
        prompt="open youtube", context=context, category="voice",
    )

    assert response == "Opening youtube."
    tool.ainvoke.assert_awaited_once_with({"query": "youtube"})
    coordinator._preload_main_model.assert_not_called()
    coordinator._history_loader.load.assert_not_called()


@pytest.mark.asyncio
async def test_fast_lane_miss_falls_through_to_normal_pipeline():
    """An ambiguous request must still reach history load / mode resolution —
    proves the short circuit doesn't swallow turns it can't handle."""
    registry, _ = _registry_with("open_browser")
    coordinator = _build_coordinator(registry)
    context = RequestContext(platform="voice", session_id="s2")

    with pytest.raises(AssertionError, match="history load must not run"):
        await coordinator._execute_turn_internal(
            prompt="check my email for the invoice from last week",
            context=context,
            category="voice",
        )


@pytest.mark.asyncio
async def test_non_voice_category_never_consults_fast_lane():
    """A regular agent-category turn with text that WOULD fast-lane-match as voice
    must not be short-circuited — the fast lane is voice-only."""
    registry, tool = _registry_with("open_browser")
    coordinator = _build_coordinator(registry)
    context = RequestContext(platform="gui", session_id="s3")

    with pytest.raises(AssertionError, match="history load must not run"):
        await coordinator._execute_turn_internal(
            prompt="open youtube", context=context, category="agent",
        )
    tool.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_polite_open_hits_tier1_fast_lane():
    """Polite wrappers used to miss ``^open `` and fall through to 9B/Gemma."""
    registry, tool = _registry_with("open_browser")
    coordinator = _build_coordinator(registry)
    context = RequestContext(platform="voice", session_id="s-polite")

    response = await coordinator._execute_turn_internal(
        prompt="can you open youtube", context=context, category="voice",
    )

    assert response == "Opening youtube."
    tool.ainvoke.assert_awaited_once_with({"query": "youtube"})
    coordinator._preload_main_model.assert_not_called()


@pytest.mark.asyncio
async def test_quick_tool_lane_hit_when_fast_lane_misses(monkeypatch):
    """Tier 2 still catches phrasing regex cannot (no open/search verb)."""
    registry, _ = _registry_with("open_browser")
    coordinator = _build_coordinator(registry)
    context = RequestContext(platform="voice", session_id="s4")

    async def fake_try_quick_tool_call(text, tool_registry, *, model, base_url, timeout=8.0):
        assert text == "navigate to youtube"
        return "Opening youtube."

    monkeypatch.setattr(
        "src.core.orchestration.nodes.voice_quick_tool.try_quick_tool_call",
        fake_try_quick_tool_call,
    )

    response = await coordinator._execute_turn_internal(
        prompt="navigate to youtube", context=context, category="voice",
    )

    assert response == "Opening youtube."
    coordinator._preload_main_model.assert_not_called()
    coordinator._history_loader.load.assert_not_called()


@pytest.mark.asyncio
async def test_both_tiers_miss_falls_through_to_normal_pipeline(monkeypatch):
    registry, _ = _registry_with("open_browser")
    coordinator = _build_coordinator(registry)
    context = RequestContext(platform="voice", session_id="s5")

    async def fake_try_quick_tool_call(text, tool_registry, *, model, base_url, timeout=8.0):
        return None

    monkeypatch.setattr(
        "src.core.orchestration.nodes.voice_quick_tool.try_quick_tool_call",
        fake_try_quick_tool_call,
    )

    with pytest.raises(AssertionError, match="history load must not run"):
        await coordinator._execute_turn_internal(
            prompt="check my email for the invoice from last week",
            context=context,
            category="voice",
        )


@pytest.mark.asyncio
async def test_quick_tool_lane_never_consulted_for_non_voice(monkeypatch):
    registry, _ = _registry_with("open_browser")
    coordinator = _build_coordinator(registry)
    context = RequestContext(platform="gui", session_id="s6")

    called = {"hit": False}

    async def fake_try_quick_tool_call(text, tool_registry, *, model, base_url, timeout=8.0):
        called["hit"] = True
        return "should not be reached"

    monkeypatch.setattr(
        "src.core.orchestration.nodes.voice_quick_tool.try_quick_tool_call",
        fake_try_quick_tool_call,
    )

    with pytest.raises(AssertionError, match="history load must not run"):
        await coordinator._execute_turn_internal(
            prompt="can you open youtube", context=context, category="agent",
        )
    assert called["hit"] is False
