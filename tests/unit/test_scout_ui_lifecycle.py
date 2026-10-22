from unittest.mock import MagicMock, AsyncMock

import pytest
from src.core.orchestration.agent_scope import agent_scope
from src.core.orchestration.context import parent_correlation_id_var
from src.core.orchestration.nodes.scout_node import _emit_scout_lifecycle, _execute_scout_tool


def test_scout_header_is_root_while_inner_execution_keeps_parent():
    bus = MagicMock()
    with agent_scope('Scout', parent_id='dispatch-123', set_parent=True):
        _emit_scout_lifecycle(bus, 'dispatch-123', start=True, prompt='Gather facts')
        _emit_scout_lifecycle(bus, 'dispatch-123', start=False, result={'gather_brief': 'Observed facts', 'result': 'Observed facts'})
        assert parent_correlation_id_var.get() == 'dispatch-123'
    start, end = [c.args[0] for c in bus.publish.call_args_list]
    assert start.parent_id is None and end.parent_id is None
    assert start.correlation_id == end.correlation_id == 'dispatch-123'
    assert start.tool_name == end.tool_name == 'dispatch_scout'
    assert end.result['gather_brief'] == 'Observed facts'


@pytest.mark.asyncio
async def test_scout_inner_tool_has_one_consistent_correlation_and_parent(monkeypatch):
    from src.application.events.event_bus import EventBus
    bus = MagicMock()
    monkeypatch.setattr(EventBus, 'get_instance', lambda: bus)
    fake = MagicMock()
    fake.ainvoke = AsyncMock(return_value={'success': True, 'result': 'found'})
    await _execute_scout_tool('read_file', {'path': 'fixture'}, {'read_file': fake}, MagicMock(), {}, 'scout-root')
    start, end = [c.args[0] for c in bus.publish.call_args_list]
    assert start.parent_id == end.parent_id == 'scout-root'
    assert start.correlation_id == end.correlation_id
    assert fake.ainvoke.call_args.kwargs['config']['metadata']['tool_call_id'] == start.correlation_id


def test_step_limits_resolve_consistently_and_keep_positive_bounds(monkeypatch):
    from src.shared.config_manager import ConfigManager
    from src.core.orchestration.agents.specialist_graph import get_specialist_max_steps
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None: default)
    assert get_specialist_max_steps('ui_automation_agent') == 40
    assert get_specialist_max_steps('browser_agent') == 60
    assert get_specialist_max_steps('system_agent') == 30
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None: 5 if key == 'tools.browser_max_actions_per_task' else default)
    assert get_specialist_max_steps('browser_agent') == 5


@pytest.mark.asyncio
async def test_browser_handoff_recursion_allows_the_configured_step_budget(monkeypatch):
    from langchain_core.messages import AIMessage
    from src.core.orchestration.handoffs import create_subagent_adapter
    import src.core.orchestration.handoffs as handoffs
    monkeypatch.setattr(handoffs, '_swap_for_subagent', AsyncMock(return_value=None))
    monkeypatch.setattr(handoffs, '_swap_back', AsyncMock())
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=None)
    graph.ainvoke = AsyncMock(return_value={'messages': [AIMessage(content='observed')]})
    node = create_subagent_adapter('browser_agent', graph)
    await node({'messages': [AIMessage(content='', tool_calls=[{
        'name': 'delegate_to_browser_agent', 'args': {'task': 'bounded browse'}, 'id': 'browser',
    }])]}, {'configurable': {'thread_id': 'cap-budget'}})
    assert graph.ainvoke.call_args.kwargs['config']['recursion_limit'] >= 124


@pytest.mark.asyncio
async def test_ui_specialist_can_continue_past_old_ten_round_cap(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from src.core.orchestration.agents.specialist_graph import build_specialist_graph
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None: default)

    @tool
    async def observation_fixture() -> dict:
        """An offline observation with no external actions."""
        return {'success': True, 'result': 'state observed'}

    class Model:
        calls = 0
        def bind_tools(self, tools):
            return self
        async def ainvoke(self, messages, config=None):
            self.calls += 1
            if self.calls <= 12:
                return AIMessage(content='', tool_calls=[{
                    'name': 'observation_fixture', 'args': {}, 'id': f'obs-{self.calls}',
                }])
            return AIMessage(content='Verified outcome.')

    model = Model()
    graph = build_specialist_graph(model, [observation_fixture], 'offline test', name='ui_automation_agent')
    result = await graph.ainvoke({'messages': [('human', 'observe task')]}, {'recursion_limit': 84})
    assert result['specialist_steps'] == 12
    assert not result.get('budget_exceeded')
    assert result['messages'][-1].content == 'Verified outcome.'
