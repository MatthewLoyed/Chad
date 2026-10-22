"""Offline regressions from the 18:46 Bonsai GUI run; no external actions."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from src.core.orchestration import approval_store
from src.core.orchestration.agents.specialist_graph import build_specialist_graph
from src.core.orchestration.approval_gate import _preview_params
from src.core.orchestration.context import session_id_var, pending_approval_var
from src.core.orchestration.handoffs import _desktop_evidence, create_subagent_adapter
from src.core.tools.repetition import RepetitionGuard
from src.infrastructure.tools.execution.executor_strategy import LocalExecutor, MCPExecutor
from src.tools.base_tool import BaseTool


class SendFixture(BaseTool):
    name = 'send_email'
    description = 'Fake send for gate testing.'
    requires_approval = True

    def get_schema(self):
        return {'name': self.name, 'parameters': {'type': 'object', 'properties': {}}}

    async def execute(self, **kwargs):
        raise AssertionError('An unapproved send must never run')


class PausingModel:
    def __init__(self):
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        self.calls += 1
        assert self.calls == 1, 'Approval must pause before the model advances the plan'
        return AIMessage(content='', tool_calls=[{'name': 'send_email', 'args': {}, 'id': 'send'}])


@pytest.mark.asyncio
async def test_real_executor_runtime_params_cannot_break_approval_pause():
    fake = SendFixture()
    executor = LocalExecutor()
    async def invoke_fake():
        return await executor.execute(fake, {
            'to': 'person@example.com', 'tool_registry': object(),
            'other_runtime_object': object(), 'nonfinite': float('nan'),
        })
    wrapped = StructuredTool.from_function(coroutine=invoke_fake, name='send_email', description='Test send gate')
    model = PausingModel()
    graph = build_specialist_graph(model, [wrapped], 'Test system', checkpointer=MemorySaver(), name='gate-test')
    session = session_id_var.set('json-gate-regression')
    pending = pending_approval_var.set(None)
    minted = None
    try:
        result = await graph.ainvoke({'messages': [HumanMessage(content='perform task')]},
                                    {'configurable': {'thread_id': 'json-gate-regression'}})
        minted = result['pending_approval']['token']
        payload = approval_store.peek(minted)['payload']
        assert payload['params'] == {'to': 'person@example.com'}
        json.dumps(payload, allow_nan=False)
        assert model.calls == 1
        tool_result = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        assert json.loads(tool_result.content)['_pending_approval'] is True
    finally:
        if minted:
            approval_store.consume(minted)
        pending_approval_var.reset(pending)
        session_id_var.reset(session)


def test_preview_drops_nonserializable_values_without_leaking_repr():
    assert _preview_params({'tool_registry': object(), 'runtime': {'nested': object()},
                            '_token': 'private', 'to': 'person@example.com'}) == {'to': 'person@example.com'}


@pytest.mark.parametrize('name,args', [
    ('screenshot_control', {'annotate': True, 'includeImage': False}),
    ('ui_find', {'windowHandle': '123', 'name': 'Launch'}),
    ('window_management', {'action': 'list'}),
])
def test_desktop_observation_can_refresh_after_repeated_success_and_failure(name, args):
    guard = RepetitionGuard()
    for success in [True, True, True, False, False]:
        guard.note(name, args, success)
        assert guard.check(name, args) is None
    assert 'reuse it' not in (guard.note(name, args, True) or '')


def test_desktop_mutation_is_still_guarded():
    guard = RepetitionGuard()
    args = {'action': 'close', 'handle': '123'}
    guard.note('window_management', args, False)
    assert guard.check('window_management', args) is not None


@pytest.mark.asyncio
async def test_click_acknowledges_actual_target_without_claiming_task_success(monkeypatch):
    import src.infrastructure.tools.execution.executor_strategy as execution
    monkeypatch.setattr(execution, '_policy_approval_gate', lambda *args: None)
    server = MagicMock()
    server.name = 'windows-semantic-ui'
    server.call_tool = AsyncMock(return_value=[{'type': 'text', 'text': json.dumps({
        'success': True, 'action': 'click',
        'items': [{'id': '52', 'name': 'Prism Launcher Release', 'type': 'Button', 'enabled': True}],
    })}])
    result = await MCPExecutor().execute({'name': 'ui_click', 'mcp_server': server}, {'windowHandle': '123', 'nameContains': 'Launch'})
    assert result['success'] is True
    assert not result.get('terminal')
    text = str(result['result'])
    assert 'Prism Launcher Release' in text
    assert 'does not prove' in text
    assert 'This action is definitive' not in text


def ui_receipt(call_id='click', target='Prism Launcher Release'):
    return ToolMessage(name='ui_click', tool_call_id=call_id, content='[TOOL_DATA]\n' + json.dumps({
        'success': True, 'result': [{'type': 'text', 'text': json.dumps({
            'success': True, 'action': 'click', 'items': [
                {'id': '52', 'name': target, 'type': 'Button', 'enabled': False},
            ],
        })}],
    }))


def test_ui_evidence_preserves_actual_target_and_excludes_prior_delegation():
    messages = [HumanMessage(content='old task'), ui_receipt('old', 'old sensitive target'),
                HumanMessage(content='new task'), ui_receipt(), AIMessage(content='Launch succeeded')]
    evidence = _desktop_evidence(messages, 'new task')
    assert 'Prism Launcher Release' in evidence
    assert '"enabled": false' in evidence
    assert '"call_id": "click"' in evidence
    assert 'old sensitive target' not in evidence
    assert _desktop_evidence(messages, 'absent task') == ''


def test_large_ui_receipts_remain_parseable_and_bounded():
    messages = [HumanMessage(content='current')] + [ui_receipt(str(i), 'Long target ' * 2000) for i in range(15)]
    evidence = _desktop_evidence(messages, 'current')
    records = [json.loads(line) for line in evidence.splitlines() if line.startswith('{')]
    assert len(records) == 6
    assert all(r['excerpted'] for r in records)
    assert records[-1]['call_id'] == '14'
    assert len(evidence) < 7000


@pytest.mark.asyncio
async def test_graph_handoff_forwards_ui_receipts_beside_summary(monkeypatch):
    import src.core.orchestration.handoffs as handoffs
    monkeypatch.setattr(handoffs, '_swap_for_subagent', AsyncMock(return_value=None))
    monkeypatch.setattr(handoffs, '_swap_back', AsyncMock())
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=None)
    graph.ainvoke = AsyncMock(return_value={'messages': [HumanMessage(content='launch instance'),
        ui_receipt(), AIMessage(content='Launch clicked.')]})
    node = create_subagent_adapter('ui_automation_agent', graph)
    result = await node({'messages': [AIMessage(content='', tool_calls=[{
        'name': 'delegate_to_ui_automation_agent', 'args': {'task': 'launch instance'}, 'id': 'delegate',
    }])]}, {'configurable': {'thread_id': 'ui-evidence'}})
    text = result['messages'][0].content
    assert 'Launch clicked.' in text
    assert 'Prism Launcher Release' in text
    assert 'UI TOOL EVIDENCE' in text
