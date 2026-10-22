"""Subagent handoff happy-path coverage for issue #216.

The original #216 checklist named "workspace_agent" as the delegation target,
but that agent no longer exists in AGENT_CONFIGS (renamed/consolidated in
later refactors -- see load_skill.py's docstring, the only surviving mention
of the name). system_agent is the current equivalent ("local file system
operations... managing OS settings" -- workspace-style tasks), so these tests
exercise create_handoff_tools' delegate_to_system_agent path: supervisor
delegates a task, the specialist's result is forwarded back correctly.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.core.orchestration.handoffs import create_handoff_tools


def _make_fake_specialist(final_text: str):
    """A stand-in CompiledGraph: aget_state (no prior history) + ainvoke (returns final_text)."""
    agent = MagicMock()
    agent.aget_state = AsyncMock(return_value=None)
    agent.ainvoke = AsyncMock(
        return_value={"messages": [HumanMessage(content="task"), AIMessage(content=final_text)]}
    )
    return agent


def _get_tool(tools, name):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name} not found among {[t.name for t in tools]}")


@pytest.mark.asyncio
async def test_handoff_delegates_and_forwards_specialist_result():
    specialist = _make_fake_specialist("Freed up 4GB by clearing temp files.")
    tools = create_handoff_tools({"system_agent": specialist})
    delegate_tool = _get_tool(tools, "delegate_to_system_agent")

    config = {"configurable": {"thread_id": "parent-thread"}, "metadata": {}}
    result = await delegate_tool.coroutine("clean up temp files to free disk space", config)

    # The specialist's natural-language answer is forwarded verbatim to the supervisor.
    assert result == "Freed up 4GB by clearing temp files."

    # The specialist was actually invoked with the task as a HumanMessage plus a
    # system-identity message (since no prior sub-thread history existed).
    specialist.ainvoke.assert_awaited_once()
    call_args = specialist.ainvoke.call_args
    sub_input = call_args.args[0]
    sub_config = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("config")
    messages = sub_input["messages"]
    assert any(isinstance(m, SystemMessage) for m in messages)
    assert any(isinstance(m, HumanMessage) and "clean up temp files" in m.content for m in messages)

    # The sub-agent gets its own namespaced thread_id, not the parent's, to avoid state bleed.
    assert sub_config["configurable"]["thread_id"] == "parent-thread_system_agent"


@pytest.mark.asyncio
async def test_handoff_skips_system_message_when_subagent_thread_already_has_one():
    specialist = _make_fake_specialist("Done.")
    specialist.aget_state = AsyncMock(
        return_value=MagicMock(values={"messages": [SystemMessage(content="ROLE: existing")]})
    )
    tools = create_handoff_tools({"system_agent": specialist})
    delegate_tool = _get_tool(tools, "delegate_to_system_agent")

    config = {"configurable": {"thread_id": "parent-thread"}, "metadata": {}}
    await delegate_tool.coroutine("second task on the same sub-thread", config)

    sub_input = specialist.ainvoke.call_args.args[0]
    assert not any(isinstance(m, SystemMessage) for m in sub_input["messages"])
    assert len(sub_input["messages"]) == 1


@pytest.mark.asyncio
async def test_handoff_surfaces_specialist_crash_as_error_string_not_exception():
    specialist = _make_fake_specialist("unused")
    specialist.ainvoke = AsyncMock(side_effect=RuntimeError("sandbox unreachable"))
    tools = create_handoff_tools({"system_agent": specialist})
    delegate_tool = _get_tool(tools, "delegate_to_system_agent")

    config = {"configurable": {"thread_id": "parent-thread"}, "metadata": {}}
    result = await delegate_tool.coroutine("do something", config)

    # A specialist crash must degrade to an error string the supervisor can react to,
    # never propagate as an unhandled exception that would abort the whole turn.
    assert "sandbox unreachable" in result


@pytest.mark.asyncio
async def test_swap_back_awaits_reload(monkeypatch):
    from src.core.orchestration.handoffs import _swap_back, OWNER_MAIN_MODEL

    release_mock = AsyncMock()
    ensure_resident_mock = AsyncMock()
    monkeypatch.setattr("src.core.orchestration.handoffs.release", release_mock)
    monkeypatch.setattr("src.core.orchestration.handoffs.ensure_resident", ensure_resident_mock)

    # 1. Swapped is None
    await _swap_back(None)
    release_mock.assert_not_called()
    ensure_resident_mock.assert_not_called()

    # 2. Valid swap tuple
    swapped = ("http://main:1234", "main-model", "http://sub:1234", "sub-model", "traversal_agent")
    await _swap_back(swapped)

    release_mock.assert_awaited_once_with("http://sub:1234", "sub-model", owner="traversal_agent")
    ensure_resident_mock.assert_awaited_once()
    assert ensure_resident_mock.call_args.args[0] == "http://main:1234"
    assert ensure_resident_mock.call_args.args[1] == "main-model"
    assert ensure_resident_mock.call_args.kwargs["owner"] == OWNER_MAIN_MODEL


@pytest.mark.asyncio
async def test_swap_back_handles_exceptions_gracefully(monkeypatch):
    from src.core.orchestration.handoffs import _swap_back

    release_mock = AsyncMock(side_effect=RuntimeError("release failed"))
    ensure_resident_mock = AsyncMock(side_effect=RuntimeError("load failed"))
    monkeypatch.setattr("src.core.orchestration.handoffs.release", release_mock)
    monkeypatch.setattr("src.core.orchestration.handoffs.ensure_resident", ensure_resident_mock)

    swapped = ("http://main:1234", "main-model", "http://sub:1234", "sub-model", "traversal_agent")
    # Should not raise exception
    await _swap_back(swapped)
    release_mock.assert_awaited_once()
    ensure_resident_mock.assert_awaited_once()

