"""
Issue #325 follow-up: the first #325 fix made a specialist's own graph halt on
`_pending_approval` and forward the payload inside its ToolMessage content, but
`subagent_node` (handoffs.py, the node actually wired into the supervisor graph
via `workflow.add_node(agent_name, subagent_node)`) never surfaced it as a
TOP-LEVEL state key. `workflow.add_edge(agent_name, "supervisor")` in
supervisor.py was also unconditional, so the supervisor's own model just kept
going and narrated "waiting on your input" instead of the turn actually
pausing for a real ApprovalCard.

This covers the two-part fix: `subagent_node` sets `pending_approval` (and
`final_response`) on its returned state dict, and supervisor.py's edge for
agent_name nodes is now conditional (route_after_tools) instead of
unconditional, so it exits to the user exactly like the "tools" node does.
"""

import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.handoffs import create_subagent_adapter
from src.core.orchestration.nodes.routing import route_after_tools


class _FakeSubgraph:
    def __init__(self, ainvoke_result):
        self._result = ainvoke_result

    async def aget_state(self, config):
        return None

    async def ainvoke(self, sub_agent_input, config):
        return self._result


def _delegate_call_state(agent_name: str):
    msg = AIMessage(
        content="",
        tool_calls=[{"name": f"delegate_to_{agent_name}", "args": {"task": "click submit"}, "id": "call1"}],
    )
    return {"messages": [msg]}


@pytest.mark.asyncio
class TestSubagentNodePendingApproval:
    async def test_pending_approval_surfaces_as_top_level_state_key(self, monkeypatch):
        import src.core.orchestration.handoffs as handoffs

        async def _noop_swap(agent_name):
            return None

        async def _noop_swap_back(swapped):
            return None

        monkeypatch.setattr(handoffs, "_swap_for_subagent", _noop_swap)
        monkeypatch.setattr(handoffs, "_swap_back", _noop_swap_back)

        pending = {
            "token": "CHAD-T1",
            "tool_name": "browser_click",
            "decision_type": "accept_decline",
            "prompt": "Allow browser_click on example.com?",
            "options": None,
            "title": "Browser action needs approval",
            "questions": None,
        }
        fake_graph = _FakeSubgraph({"messages": [AIMessage(content="")], "pending_approval": pending})
        node = create_subagent_adapter("browser_agent", fake_graph)

        state = _delegate_call_state("browser_agent")
        config = {"configurable": {"thread_id": "conv-1"}}
        result = await node(state, config)

        # The critical assertion the original #325 fix missed: pending_approval
        # must be a TOP-LEVEL key on the node's returned state, not just buried
        # inside the ToolMessage content string.
        assert result.get("pending_approval") == pending
        assert result.get("final_response")

        # And the conditional edge (supervisor.py) must exit instead of looping.
        assert route_after_tools(result) == "__end__"

    async def test_normal_completion_does_not_set_pending_approval(self, monkeypatch):
        import src.core.orchestration.handoffs as handoffs

        async def _noop_swap(agent_name):
            return None

        async def _noop_swap_back(swapped):
            return None

        monkeypatch.setattr(handoffs, "_swap_for_subagent", _noop_swap)
        monkeypatch.setattr(handoffs, "_swap_back", _noop_swap_back)

        fake_graph = _FakeSubgraph({"messages": [AIMessage(content="All done, found the price: $10.")]})
        node = create_subagent_adapter("browser_agent", fake_graph)

        state = _delegate_call_state("browser_agent")
        config = {"configurable": {"thread_id": "conv-2"}}
        result = await node(state, config)

        assert result.get("pending_approval") is None
        assert route_after_tools(result) == "supervisor"


class _CrashingSubgraph:
    """Simulates a specialist subgraph that raises instead of returning state."""

    async def aget_state(self, config):
        return None

    async def ainvoke(self, sub_agent_input, config):
        raise RuntimeError("specialist blew up mid-tool-call")


@pytest.mark.asyncio
class TestSubagentNodeCrashHandling:
    """Refs #216: a crash inside a specialist subgraph must degrade gracefully
    (a ToolMessage the supervisor's model can react to) instead of propagating
    and aborting the whole turn."""

    async def test_subagent_crash_returns_error_tool_message_not_raise(self, monkeypatch):
        import src.core.orchestration.handoffs as handoffs

        async def _noop_swap(agent_name):
            return None

        async def _noop_swap_back(swapped):
            return None

        monkeypatch.setattr(handoffs, "_swap_for_subagent", _noop_swap)
        monkeypatch.setattr(handoffs, "_swap_back", _noop_swap_back)

        node = create_subagent_adapter("browser_agent", _CrashingSubgraph())

        state = _delegate_call_state("browser_agent")
        config = {"configurable": {"thread_id": "conv-3"}}

        # Must not raise — the crash is caught and surfaced as tool output.
        result = await node(state, config)

        assert result.get("pending_approval") is None
        assert route_after_tools(result) == "supervisor"
        tool_message = result["messages"][0]
        assert "specialist blew up mid-tool-call" in tool_message.content
        # A delegate crash is rendered in the same failure envelope as any tool
        # failure, so the supervisor has one shape to react to.
        assert "TOOL_FAILED[" in tool_message.content
        assert "NEXT_ACTIONS" in tool_message.content
