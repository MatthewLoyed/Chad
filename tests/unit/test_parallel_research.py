"""Unit tests for the parallel_research fan-out tool (issue #508).

Verifies the orchestrator/worker map-reduce contract WITHOUT touching LM Studio:
- one worker per brief, each on a UNIQUE namespaced thread_id (no checkpointer collision),
- workers run CONCURRENTLY (not serial),
- a single worker's failure/timeout degrades to a noted result instead of sinking the batch,
- the combined output labels every item so the supervisor can synthesize,
- the model swap/preload lifecycle is invoked exactly once for the whole batch (~2 swaps).
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from src.core.orchestration import handoffs


class _MockResearchGraph:
    """Records each worker invocation and tracks peak concurrency."""

    def __init__(self, per_call_delay=0.05, fail_on=None):
        self.per_call_delay = per_call_delay
        self.fail_on = fail_on or set()
        self.threads = []
        self.active = 0
        self.peak_active = 0

    async def ainvoke(self, inputs, config=None):
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        thread = (config or {}).get("configurable", {}).get("thread_id")
        self.threads.append(thread)
        try:
            await asyncio.sleep(self.per_call_delay)
            brief = inputs["messages"][-1].content
            if any(marker in brief for marker in self.fail_on):
                raise RuntimeError("worker boom")
            return {"messages": [AIMessage(content=f"SUMMARY::{brief[:20]}")]}
        finally:
            self.active -= 1


@pytest.fixture(autouse=True)
def _isolate_model_lifecycle(monkeypatch):
    """No real LM Studio: stub the swap/residency calls the tool makes."""
    async def _noop_ensure(*a, **k):
        return True

    async def _noop_release(*a, **k):
        return None

    swaps = {"for": 0, "back": 0}

    async def _swap_for(agent_name):
        swaps["for"] += 1
        return ("http://x", "main-model")

    async def _swap_back(swapped):
        swaps["back"] += 1

    monkeypatch.setattr(handoffs, "ensure_resident", _noop_ensure)
    monkeypatch.setattr(handoffs, "release", _noop_release)
    monkeypatch.setattr(handoffs, "_swap_for_subagent", _swap_for)
    monkeypatch.setattr(handoffs, "_swap_back", _swap_back)
    monkeypatch.setattr(handoffs, "_research_worker_model", lambda: ("http://x", "worker-9b"))
    return swaps


def _tool_coro(graph):
    tool = handoffs.create_parallel_research_tool(graph)
    assert tool.name == "parallel_research"
    return tool.coroutine


@pytest.mark.asyncio
async def test_fans_out_one_worker_per_brief_concurrently(_isolate_model_lifecycle):
    graph = _MockResearchGraph(per_call_delay=0.1)
    coro = _tool_coro(graph)
    briefs = [f"Research item {i}: fields A,B. Return with URL." for i in range(4)]

    config = {"configurable": {"thread_id": "conv123"}}
    out = await coro(research_briefs=briefs, config=config)

    # one worker per brief
    assert len(graph.threads) == 4
    # unique, namespaced thread ids (no checkpointer collision)
    assert sorted(graph.threads) == [f"conv123_research_worker_{i}" for i in range(4)]
    assert len(set(graph.threads)) == 4
    # actually ran concurrently (all 4 in one batch, cap default 6)
    assert graph.peak_active == 4
    # every item labeled in the combined output
    for i in range(1, 5):
        assert f"### Item {i}" in out
    assert "SUMMARY::" in out
    # ~2 swaps for the whole batch, not per-item
    assert _isolate_model_lifecycle == {"for": 1, "back": 1}


@pytest.mark.asyncio
async def test_one_worker_failure_does_not_sink_batch(_isolate_model_lifecycle):
    graph = _MockResearchGraph(per_call_delay=0.01, fail_on={"item 1:"})
    coro = _tool_coro(graph)
    briefs = ["Research item 0: x", "Research item 1: x", "Research item 2: x"]

    out = await coro(research_briefs=briefs, config={"configurable": {"thread_id": "c"}})

    assert "### Item 1" in out and "### Item 2" in out and "### Item 3" in out
    assert "failed" in out.lower()  # the boom worker is noted, others still summarized
    assert out.count("SUMMARY::") == 2
    # swap-back still happened despite the failure
    assert _isolate_model_lifecycle["back"] == 1


@pytest.mark.asyncio
async def test_empty_briefs_returns_error_without_swapping(_isolate_model_lifecycle):
    graph = _MockResearchGraph()
    coro = _tool_coro(graph)

    out = await coro(research_briefs=["  ", ""], config={"configurable": {"thread_id": "c"}})

    assert out.startswith("ERROR")
    assert graph.threads == []
    assert _isolate_model_lifecycle == {"for": 0, "back": 0}


@pytest.mark.asyncio
async def test_batches_when_more_briefs_than_cap(monkeypatch, _isolate_model_lifecycle):
    monkeypatch.setattr(handoffs, "_parallel_research_cap", lambda: 3)
    graph = _MockResearchGraph(per_call_delay=0.05)
    coro = _tool_coro(graph)
    briefs = [f"Research item {i}: x" for i in range(7)]

    out = await coro(research_briefs=briefs, config={"configurable": {"thread_id": "c"}})

    assert len(graph.threads) == 7
    # never more than the cap concurrently, but the batch still parallelizes
    assert graph.peak_active <= 3
    assert graph.peak_active >= 2
    for i in range(1, 8):
        assert f"### Item {i}" in out


def _discover_coro(graph):
    tool = handoffs.create_research_discover_tool(graph)
    assert tool.name == "research_discover"
    return tool.coroutine


@pytest.mark.asyncio
async def test_research_discover_is_a_single_worker_call(_isolate_model_lifecycle):
    """Breadth discovery (#510) is ONE call, not a fan-out — distinct from parallel_research."""
    graph = _MockResearchGraph(per_call_delay=0.05)
    coro = _discover_coro(graph)

    out = await coro(task="List 10 real wedding caterers near Nashville, TN.",
                      config={"configurable": {"thread_id": "conv123"}})

    assert len(graph.threads) == 1
    assert graph.threads == ["conv123_research_discover"]
    assert "SUMMARY::" in out
    # ~1 swap for the single call, same lifecycle pattern as parallel_research
    assert _isolate_model_lifecycle == {"for": 1, "back": 1}


@pytest.mark.asyncio
async def test_research_discover_empty_task_returns_error_without_swapping(_isolate_model_lifecycle):
    graph = _MockResearchGraph()
    coro = _discover_coro(graph)

    out = await coro(task="   ", config={"configurable": {"thread_id": "c"}})

    assert out.startswith("ERROR")
    assert graph.threads == []
    assert _isolate_model_lifecycle == {"for": 0, "back": 0}


@pytest.mark.asyncio
async def test_research_discover_timeout_does_not_raise(monkeypatch, _isolate_model_lifecycle):
    monkeypatch.setattr(handoffs, "_parallel_worker_timeout", lambda: 0.01)
    graph = _MockResearchGraph(per_call_delay=1.0)
    coro = _discover_coro(graph)

    out = await coro(task="List 10 real wedding caterers near Nashville, TN.",
                      config={"configurable": {"thread_id": "c"}})

    assert "timed out" in out.lower()
    # swap-back still happened despite the timeout
    assert _isolate_model_lifecycle["back"] == 1


@pytest.mark.asyncio
async def test_event_stream_suppresses_research_worker_thinking():
    from src.core.orchestration.graph.event_stream import run_turn_stream

    class MockStreamGraph:
        async def aget_state(self, config):
            return None

        async def astream_events(self, input_data, config=None, version="v2"):
            # Orchestrator thought
            yield {
                "event": "on_custom_event",
                "name": "agent_thinking",
                "data": {"thinking": "Deep research plan", "agent_name": "Deep Research", "type": "reasoning"},
            }
            # Interleaved worker thoughts
            yield {
                "event": "on_custom_event",
                "name": "agent_thinking",
                "data": {"thinking": "Worker 1 scratchpad", "agent_name": "Research Worker 1", "type": "reasoning"},
            }
            yield {
                "event": "on_custom_event",
                "name": "agent_thinking",
                "data": {"thinking": "Worker 2 scratchpad", "agent_name": "Research Worker 2", "type": "reasoning"},
            }
            # Orchestrator synthesis thought
            yield {
                "event": "on_custom_event",
                "name": "agent_thinking",
                "data": {"thinking": "Synthesizing report", "agent_name": "Deep Research", "type": "reasoning"},
            }
            yield {
                "event": "on_chain_end",
                "data": {"output": {"messages": [AIMessage(content="Final Report")]}},
            }

    received_thoughts = []

    async def on_thinking(text, type="reasoning", append=True, agent_name=None, parent_id=None):
        received_thoughts.append((text, agent_name))

    state = await run_turn_stream(
        MockStreamGraph(),
        config={},
        initial_state={"messages": []},
        effective_thread_id=None,
        on_thinking=on_thinking,
    )

    assert [t[0] for t in received_thoughts] == ["Deep research plan", "Synthesizing report"]
    assert [t[1] for t in received_thoughts] == ["Deep Research", "Deep Research"]
