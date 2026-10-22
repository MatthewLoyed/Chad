"""
Issue #279 follow-up: specialist subagents (browser_agent, etc.) were built on
langgraph.prebuilt.create_react_agent, which has no concept of `_pending_approval` —
a gated write action's pause JSON just got fed back into the specialist's own model
and the ReAct loop continued, so no ApprovalCard ever reached the UI and the model
was never actually stopped. build_specialist_graph (specialist_graph.py) replaces it
with a small custom graph that halts immediately when a tool call sets
`pending_approval`, mirroring the pattern already proven for the supervisor's own
graph (nodes/tool_node.py + nodes/routing.py) and the scout gather loop.

Also covers a regression these tests caught during development: SanitizedToolNode's
[TOOL_DATA] injection-hardening prefix (agent_utils.py) breaks json.loads() on the
control-signal envelope, which silently swallowed `_pending_approval` and caused an
infinite loop — fixed by exempting control-signal JSON from sanitization.
"""

import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.agents.agent_utils import sanitize_tool_output, _is_control_signal
from src.core.orchestration.agents.specialist_graph import build_specialist_graph


@tool
async def pending_approval_tool(x: str) -> dict:
    """A tool whose call is gated and must pause."""
    return {
        "success": True,
        "_pending_approval": True,
        "approval_token": "CHAD-TEST",
        "decision_type": "accept_decline",
        "result": "Allow it to proceed?",
        "options": None,
        "title": "Needs approval",
        "questions": None,
    }


@tool
async def normal_tool(x: str) -> dict:
    """An ungated tool that just succeeds."""
    return {"success": True, "result": f"did the thing with {x}"}


@tool
async def budget_killing_tool(x: str) -> dict:
    """A tool that triggers a terminal budget limit."""
    return {
        "success": False,
        "error": "Browser kill-switch: this task has already made 41 browser actions, over the 40-action budget.",
        "_terminal_budget": True,
    }


class _ScriptedModel:
    """Fake chat model returning a scripted sequence of AIMessages, one per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


def _tool_call(name: str, call_id: str = "call1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {"x": "a"}, "id": call_id}])


class TestControlSignalExemption:
    def test_pending_approval_json_is_a_control_signal(self):
        raw = '{"success": true, "_pending_approval": true}'
        assert _is_control_signal(raw) is True

    def test_plain_text_is_not_a_control_signal(self):
        assert _is_control_signal("some page text") is False
        assert _is_control_signal('{"success": true, "result": "fine"}') is False

    def test_sanitize_still_tags_plain_output(self):
        assert sanitize_tool_output("hello").startswith("[TOOL_DATA]")

    def test_terminal_budget_json_is_a_control_signal(self):
        raw = '{"success": false, "_terminal_budget": true}'
        assert _is_control_signal(raw) is True


@pytest.mark.asyncio
class TestSpecialistGraphApprovalHalt:
    async def test_halts_immediately_on_pending_approval(self):
        model = _ScriptedModel([_tool_call("pending_approval_tool")])
        graph = build_specialist_graph(
            model, [pending_approval_tool], "system prompt", checkpointer=MemorySaver(), name="test_agent"
        )
        cfg = {"configurable": {"thread_id": "halt-test"}, "recursion_limit": 10}
        result = await graph.ainvoke({"messages": [("human", "do it")]}, config=cfg)

        assert model.calls == 1  # never looped back to the model
        pending = result.get("pending_approval")
        assert pending is not None
        assert pending["token"] == "CHAD-TEST"
        assert pending["tool_name"] == "pending_approval_tool"

    async def test_halts_after_budget_exceeded(self):
        # Even if the model tries to emit a tool call on its second turn,
        # budget_exceeded forces route_after_agent to END immediately!
        model = _ScriptedModel([
            _tool_call("budget_killing_tool"),
            _tool_call("budget_killing_tool", call_id="call2"),
        ])
        graph = build_specialist_graph(
            model, [budget_killing_tool], "system prompt", checkpointer=MemorySaver(), name="test_agent"
        )
        cfg = {"configurable": {"thread_id": "budget-test"}, "recursion_limit": 10}
        result = await graph.ainvoke({"messages": [("human", "browse")]}, config=cfg)

        assert model.calls == 2
        assert result.get("budget_exceeded") is True
        assert result.get("browser_budget_exhausted") is True

    async def test_halts_when_specialist_step_cap_reached(self, monkeypatch):
        from src.shared.config_manager import ConfigManager
        monkeypatch.setattr(ConfigManager, "get", lambda k, default=None: 2 if "specialist_max_steps" in k else default)
        # Model wants to call tool 3 times, but cap is 2
        model = _ScriptedModel([
            _tool_call("normal_tool", call_id="c1"),
            _tool_call("normal_tool", call_id="c2"),
            _tool_call("normal_tool", call_id="c3"),
        ])
        graph = build_specialist_graph(
            model, [normal_tool], "system prompt", checkpointer=MemorySaver(), name="test_agent"
        )
        cfg = {"configurable": {"thread_id": "step-cap-test"}, "recursion_limit": 10}
        result = await graph.ainvoke({"messages": [("human", "do it")]}, config=cfg)

        assert result.get("specialist_steps") == 2
        assert result.get("budget_exceeded") is True
        # Model should have been called at most 3 times (c1, c2, then unbound synthesis)
        assert model.calls <= 3

    async def test_normal_loop_completes_without_pending_approval(self):
        model = _ScriptedModel([
            _tool_call("normal_tool"),
            AIMessage(content="All done."),
        ])
        graph = build_specialist_graph(
            model, [normal_tool], "system prompt", checkpointer=MemorySaver(), name="test_agent"
        )
        cfg = {"configurable": {"thread_id": "normal-test"}, "recursion_limit": 10}
        result = await graph.ainvoke({"messages": [("human", "do it")]}, config=cfg)

        assert model.calls == 2
        assert result.get("pending_approval") is None
        assert result["messages"][-1].content == "All done."

    async def test_no_tool_call_ends_immediately(self):
        model = _ScriptedModel([AIMessage(content="No tools needed.")])
        graph = build_specialist_graph(
            model, [normal_tool], "system prompt", checkpointer=MemorySaver(), name="test_agent"
        )
        cfg = {"configurable": {"thread_id": "no-tool-test"}, "recursion_limit": 10}
        result = await graph.ainvoke({"messages": [("human", "hi")]}, config=cfg)

        assert model.calls == 1
        assert result.get("pending_approval") is None
