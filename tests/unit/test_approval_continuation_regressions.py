"""Boundary regressions from the Bonsai GUI approval trace (offline)."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from src.core.orchestration.agents.agent_utils import create_fault_tolerant_tool_node
from src.core.orchestration.agents import factory
from src.core.orchestration.graph.state import ChadState
from src.core.orchestration.handoffs import _effective_subagent_model_override
from src.core.orchestration.langgraph_engine import LangGraphEngine
from src.core.orchestration.nodes.tool_node import make_tool_node
from src.core.tools.tool_policy_manager import ToolPolicyManager


def test_real_policy_ungates_drafts_but_keeps_sending_gated():
    policy = ToolPolicyManager()
    policy.load_policies('config/tool_policies.yaml')
    assert not policy.requires_approval('create_gmail_draft')
    assert not policy.requires_approval('draft_gmail_reply')
    assert policy.requires_approval('send_email')
    assert policy.requires_approval('send_gmail_message')


def test_browser_ignores_stale_pin_in_construction_and_residency(monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.delenv('CHAD_TESTING', raising=False)
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None:
        {'browser_agent': {'model_name': 'stale-lfm'}} if key == 'subagent_model_overrides' else default)
    supervisor = object()
    registry = MagicMock()
    registry.get_langchain_tools_by_domain.return_value = []
    graph_builder = MagicMock()
    monkeypatch.setattr(factory, 'build_specialist_graph', graph_builder)
    monkeypatch.setattr(factory.SystemPromptBuilder, 'build', lambda **kwargs: 'browser instructions')
    pinned_builder = MagicMock(side_effect=AssertionError('browser must inherit supervisor'))
    monkeypatch.setattr(factory, '_build_model_override', pinned_builder)
    factory.create_specialist_agent('browser_agent', supervisor, registry)
    assert graph_builder.call_args.kwargs['model'] is supervisor
    new_supervisor = object()
    factory.create_specialist_agent('browser_agent', new_supervisor, registry)
    assert graph_builder.call_args.kwargs['model'] is new_supervisor
    assert factory.resolve_agent_model_id('browser_agent') is None
    assert _effective_subagent_model_override('browser_agent') is None


@tool
async def successful_app_fixture() -> dict:
    """Pretend an independent action completed, without touching an app."""
    return {'success': True, 'result': 'App opened.'}


@pytest.mark.asyncio
async def test_successful_tool_clears_prior_checkpoint_approval():
    workflow = StateGraph(ChadState)
    workflow.add_node('tools', make_tool_node(create_fault_tolerant_tool_node([successful_app_fixture])))
    workflow.set_entry_point('tools')
    workflow.add_edge('tools', END)
    graph = workflow.compile(checkpointer=MemorySaver())
    result = await graph.ainvoke({
        'pending_approval': {'token': 'old-draft', 'tool_name': 'create_gmail_draft'},
        'messages': [AIMessage(content='', tool_calls=[{'name': 'successful_app_fixture', 'args': {}, 'id': 'app'}])],
    }, {'configurable': {'thread_id': 'stale-approval'}})
    assert result['pending_approval'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('prior,expected', [
    ({'turn_mode': 'agent'}, 'agent'),
    ({'turn_mode': 'plan', 'active_plan': [{'status': 'running'}]}, 'agent'),
    ({'turn_mode': 'plan', 'pending_approval': {'tool_name': 'send_email'}}, 'agent'),
    ({'turn_mode': 'plan', 'pending_approval': {'tool_name': 'ask_user_question'}}, None),
    ({}, None),
])
async def test_proceed_preserves_execution_without_hijacking_planner_clarification(prior, expected):
    engine = LangGraphEngine.__new__(LangGraphEngine)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=MagicMock(values=prior))
    engine.get_graph = AsyncMock(return_value=graph)
    assert await engine.get_execution_resume_mode('same-conversation', 'proceed') == expected
    assert await engine.get_execution_resume_mode('same-conversation', 'plan a different task') is None
    graph.aget_state.assert_awaited_once_with({'configurable': {'thread_id': 'same-conversation'}})


def test_approval_payload_preserves_action_identity_and_preview():
    from src.core.orchestration import approval_store
    from src.core.orchestration.approval_gate import evaluate_approval_gate
    from src.core.orchestration.context import session_id_var, pending_approval_var
    session_token = session_id_var.set('approval-preview-test')
    pending_token = pending_approval_var.set(None)
    approval = None
    try:
        approval = evaluate_approval_gate('send_email', params={'to': 'person@example.com', 'subject': 'Meeting', '_internal': 'omit'})
        payload = pending_approval_var.get()
        assert payload['tool_name'] == 'send_email'
        assert payload['params'] == {'to': 'person@example.com', 'subject': 'Meeting'}
        assert approval.short_circuit['tool_name'] == 'send_email'
    finally:
        if approval:
            approval_store.consume(approval.short_circuit['approval_token'])
        pending_approval_var.reset(pending_token)
        session_id_var.reset(session_token)
