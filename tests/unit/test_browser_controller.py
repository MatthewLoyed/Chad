import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from src.core.orchestration.agents.browser_state import new_progress, observe, finish, delegation_input, payload
from src.core.orchestration.agents.specialist_graph import build_specialist_graph
from src.core.orchestration.context import session_id_var
from src.shared.config_manager import ConfigManager
from src.shared import captured_content
from src.core.orchestration.handoffs import _clean_subagent_response
from src.core.orchestration.agents.browser_protocol import available_tools


def decision(action, args=None, **patch):
    return {'assessment': 'initial', 'milestone': 'Find target', 'memory': 'Only observed facts.',
        'next_goal': 'Find the requested result', 'expected_change': 'Requested evidence visible',
        'action': action, 'arguments': args or {}, **patch}


class ScriptedModel:
    def __init__(self, responses):
        self.responses, self.inputs = iter(responses), []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        self.inputs.append(messages)
        value = next(self.responses)
        if callable(value):
            value = value(messages)
        if isinstance(value, AIMessage):
            return value
        return AIMessage(content='', tool_calls=[{'name': 'browser_step', 'args': value, 'id': f'call-{len(self.inputs)}'}])


@pytest.fixture(autouse=True)
def enable_controller(monkeypatch, tmp_path):
    original = ConfigManager.get
    overrides = {'orchestration.browser_controller_enabled': True,
        'orchestration.browser_agent_max_steps': 15, 'orchestration.browser_input_max_chars': 28000}
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None: overrides.get(key, original(key, default)))
    monkeypatch.setattr(captured_content, '_root', lambda: tmp_path)
    token = session_id_var.set('controller-test')
    yield
    session_id_var.reset(token)


@tool
async def browser_snapshot() -> dict:
    """Read the current page."""
    return {'success': True, 'result': [{'text': '### Page\n- Page URL: https://example.org\n- paragraph: Verification code CHAD-4729'}]}


@pytest.mark.asyncio
async def test_real_graph_dispatches_one_action_and_requires_completion_evidence():
    model = ScriptedModel([decision('browser_snapshot'), decision('browser_finish', {
        'status': 'completed', 'summary': 'Code is CHAD-4729', 'evidence': 'Verification code CHAD-4729'})])
    graph = build_specialist_graph(model, [browser_snapshot], 'Persona says browse forever.', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Read the verification code')]})
    p = result['browser_progress']
    assert p['status'] == 'completed' and p['actions'] == 1
    assert len(model.inputs) == 2
    assert 'BROWSER TASK CONTROLLER' in model.inputs[0][0].content
    assert all(len(''.join(str(m.content) for m in ms)) < 28000 for ms in model.inputs)


@pytest.mark.asyncio
async def test_minimal_action_arguments_and_legacy_assessment_do_not_break_completion():
    model = ScriptedModel([{'action': 'browser_snapshot', 'arguments': {}},
        {'action': 'browser_finish', 'arguments': {'status': 'completed', 'summary': 'CHAD-4729',
         'evidence': 'Verification code CHAD-4729'}, 'assessment': 'completed'}])
    graph = build_specialist_graph(model, [browser_snapshot], '', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Read the code')]})
    assert result['browser_progress']['status'] == 'completed'


@pytest.mark.asyncio
async def test_verified_bottom_blocks_further_scrolling_on_capture_task():
    @tool
    async def browser_scroll(direction: str) -> dict:
        """Scroll a panel."""
        raise AssertionError('Already at bottom: must inspect or capture')
    p = new_progress('Capture last response verbatim')
    p['at_bottom'] = True
    model = ScriptedModel([{'action': 'browser_scroll', 'arguments': {'direction': 'down'}},
        {'action': 'browser_finish', 'arguments': {'status': 'incomplete', 'summary': 'Need a response body target'}}])
    graph = build_specialist_graph(model, [browser_scroll], '', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Capture last response verbatim')], 'browser_progress': p})
    assert result['browser_progress']['actions'] == 0


@pytest.mark.asyncio
async def test_last_allowed_action_can_still_be_verified_without_more_browser_actions(monkeypatch):
    original = ConfigManager.get
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None:
        1 if key == 'orchestration.browser_agent_max_steps' else original(key, default))
    model = ScriptedModel([decision('browser_snapshot'), decision('browser_finish', {
        'status': 'completed', 'summary': 'CHAD-4729', 'evidence': 'Verification code CHAD-4729'})])
    graph = build_specialist_graph(model, [browser_snapshot], '', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Read code')]})
    assert result['browser_progress']['status'] == 'completed'
    assert result['browser_progress']['actions'] == 1


@pytest.mark.asyncio
async def test_prose_or_multiple_calls_are_not_implicit_success():
    bad = AIMessage(content='Done; I found everything.')
    model = ScriptedModel([bad, bad, bad, bad])
    graph = build_specialist_graph(model, [browser_snapshot], '', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Read the page')]})
    assert result['browser_progress']['status'] == 'incomplete'
    assert result['browser_progress']['actions'] == 0
    assert json.loads(result['messages'][-1].content)['browser_status'] == 'incomplete'


@pytest.mark.asyncio
async def test_unknown_action_and_bad_arguments_never_dispatch():
    model = ScriptedModel([decision('browser_run_code_unsafe'),
        decision('browser_snapshot', {'unexpected': 'bad'}), decision('browser_finish', {'status': 'incomplete', 'summary': 'Unable to proceed'})])
    graph = build_specialist_graph(model, [browser_snapshot], '', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Inspect page')]})
    assert result['browser_progress']['status'] == 'incomplete'
    assert result['browser_progress']['actions'] == 0


def test_alternating_successful_scrolls_with_repeated_state_trigger_recovery():
    p = new_progress('Find a result')
    for i in range(10):
        d = decision('browser_scroll', {'direction': 'down' if i % 2 else 'up'})
        p = observe(p, 'browser_scroll', {'success': True, 'result': [{'text': '### Page\nPage URL: https://example.org\nSame content [ref=e' + str(i) + ']'}]}, d,
                    stall_limit=2, max_recoveries=1)
        if p['status'] != 'running':
            break
    assert p['status'] == 'incomplete'
    assert p['actions'] < 10 and p['unproductive_actions'] >= 4


def test_useful_new_content_does_not_trigger_stall_or_claim_milestone_verified():
    p = new_progress('Find a result')
    for i in range(8):
        p = observe(p, 'browser_scroll', {'success': True, 'result': [{'text': f'### Page\nPage URL: https://example.org\nNew item {i}'}]}, decision('browser_scroll'))
    assert p['recoveries'] == 0 and p['status'] == 'running'
    assert p['verified_milestones'] == []


def test_fabricated_capture_or_wrong_task_capture_cannot_finish():
    p = new_progress('Capture the last response verbatim')
    meta = captured_content.save_captured_text('Example', 'https://example.org')
    with pytest.raises(ValueError):
        finish(p, {'status': 'completed', 'summary': 'Copied', 'content_id': meta['content_id']})
    p = observe(p, 'browser_capture_text', {'success': True, **meta}, decision('browser_capture_text'))
    assert finish(p, {'status': 'completed', 'summary': 'Captured', 'content_id': meta['content_id']})['status'] == 'completed'


@pytest.mark.asyncio
async def test_capture_survives_real_toolnode_sanitizer_and_supervisor_handoff():
    saved = {}
    @tool
    async def browser_capture_text(target: str) -> dict:
        """Capture the observed completed response body."""
        saved.update(captured_content.save_captured_text('Exact text.\n\nNo paraphrase. 🙏', 'https://example.org'))
        return {'success': True, **saved}
    model = ScriptedModel([decision('browser_capture_text', {'target': 'e7'}), lambda messages:
        decision('browser_finish', {'status': 'completed', 'summary': 'Captured the response.',
                                    'content_id': saved['content_id']})])
    graph = build_specialist_graph(model, [browser_capture_text], '', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Capture the final response verbatim')]})
    assert result['browser_progress']['status'] == 'completed'
    handoff = _clean_subagent_response(result['messages'])
    assert saved['content_id'] in handoff and 'VERBATIM CONTENT REFERENCES' in handoff
    assert 'No paraphrase' not in handoff
    assert len(model.inputs) == 1  # Verified capture ends the task without another LLM call.
    assert result['browser_progress']['milestone'] == 'Full text captured'
    assert result['browser_progress']['next_goal'] == ''


def test_completion_quote_must_be_in_current_observation():
    p = new_progress('Read page')
    p['observation'] = 'Correct page says 1234'
    with pytest.raises(ValueError):
        finish(p, {'status': 'completed', 'summary': 'Finished', 'evidence': '9999'})


def test_new_delegation_resets_progress_but_same_pending_approval_resumes():
    p = new_progress('Capture message', {'delegation_id': 'same'})
    p['status'] = 'completed'
    fresh = delegation_input('Capture message', 'new', p)
    assert fresh['browser_progress'] is None
    assert fresh['browser_budget_exhausted'] is False
    p['status'] = 'awaiting_approval'
    assert delegation_input('Capture message', 'same', p)['browser_progress'] == p


def test_screenshot_request_does_not_require_a_text_capture():
    assert new_progress('Capture a screenshot of this page')['contract']['deliverable'] == 'observed_result'
    assert new_progress('Capture the last completed response')['contract']['deliverable'] == 'content_id'
    tools = [SimpleNamespace(name='browser_take_screenshot'), SimpleNamespace(name='browser_run_code_unsafe')]
    assert set(available_tools(tools, 'Capture a screenshot')) == {'browser_take_screenshot'}
    assert available_tools(tools, 'Capture the last response verbatim') == {}


@pytest.mark.asyncio
async def test_approval_pause_and_resume_retries_same_action_without_model_replanning():
    calls = []
    @tool
    async def browser_click(target: str) -> dict:
        """Click the observed target."""
        calls.append(target)
        if len(calls) == 1:
            return {'_pending_approval': True, 'approval_token': 'test-approval', 'decision_type': 'accept_decline',
                    'title': 'Confirm action', 'result': 'Allow click?', 'options': None}
        return {'success': True, 'result': [{'text': '### Page\nPage URL: https://example.org\nSubmitted successfully'}]}
    model = ScriptedModel([decision('browser_click', {'target': 'e1'}),
        decision('browser_finish', {'status': 'completed', 'summary': 'Submitted', 'evidence': 'Submitted successfully'})])
    graph = build_specialist_graph(model, [browser_click], '', name='browser_agent')
    paused = await graph.ainvoke({'messages': [HumanMessage(content='Submit the requested form')]})
    assert paused['pending_approval'] and paused['browser_progress']['actions'] == 0
    assert paused['browser_progress']['status'] == 'awaiting_approval'
    # A long human pause must not exhaust the execution timeout on resume.
    paused['browser_progress']['started_at'] -= 3600
    paused['browser_progress']['approval_started_at'] -= 3600
    resumed = await graph.ainvoke(dict(paused, pending_approval=None))
    assert calls == ['e1', 'e1']
    assert resumed['browser_progress']['status'] == 'completed'
    assert len(model.inputs) == 2


@pytest.mark.asyncio
async def test_stop_cancels_inflight_action_without_dispatching_another():
    started, cancelled = asyncio.Event(), asyncio.Event()
    @tool
    async def browser_click(target: str) -> dict:
        """Click an observed target."""
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    model = ScriptedModel([decision('browser_click', {'target': 'e1'})])
    graph = build_specialist_graph(model, [browser_click], '', name='browser_agent')
    pending = asyncio.create_task(graph.ainvoke({'messages': [HumanMessage(content='Open target')]}))
    await asyncio.wait_for(started.wait(), 3)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert cancelled.is_set()
    assert len(model.inputs) == 1


@pytest.mark.asyncio
async def test_old_persisted_transcript_is_excluded_from_model_input():
    model = ScriptedModel([decision('browser_snapshot'), decision('browser_finish', {
        'status': 'completed', 'summary': 'Code is CHAD-4729', 'evidence': 'Verification code CHAD-4729'})])
    graph = build_specialist_graph(model, [browser_snapshot], '', name='browser_agent')
    await graph.ainvoke({'messages': [HumanMessage(content='OLD_TRANSCRIPT ' * 20000),
                                     HumanMessage(content='Read the verification code')],
                        'browser_task': {'objective': 'Read the verification code',
                                         'source_evidence': 'Resolved destination https://example.org',
                                         'specialist_guidance': 'Follow the active verification SOP.'}})
    assert all('OLD_TRANSCRIPT' not in str(messages) for messages in model.inputs)
    assert all(sum(len(str(m.content)) for m in messages) < 28000 for messages in model.inputs)
    assert 'Resolved destination https://example.org' in model.inputs[0][1].content
    assert 'Follow the active verification SOP.' in model.inputs[0][0].content


@pytest.mark.asyncio
async def test_large_snapshot_respects_total_context_budget(monkeypatch):
    original = ConfigManager.get
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None:
        7000 if key == 'orchestration.browser_input_max_chars' else original(key, default))
    @tool
    async def browser_snapshot() -> dict:
        """Read current page."""
        return {'success': True, 'result': '### Page\nPage URL: https://example.org\nCode CHAD-4729\n' + 'large body ' * 10000}
    model = ScriptedModel([decision('browser_snapshot'), decision('browser_finish', {
        'status': 'completed', 'summary': 'CHAD-4729', 'evidence': 'Code CHAD-4729'})])
    graph = build_specialist_graph(model, [browser_snapshot], '', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Read code')]})
    assert result['browser_progress']['status'] == 'completed'
    assert result['browser_progress']['peak_context_chars'] <= 7000


@pytest.mark.asyncio
async def test_executor_budget_stops_before_another_model_call():
    model = ScriptedModel([])
    graph = build_specialist_graph(model, [browser_snapshot], '', name='browser_agent')
    result = await graph.ainvoke({'messages': [HumanMessage(content='Read page')], 'budget_exceeded': True})
    assert result['browser_progress']['status'] == 'incomplete'
    assert not model.inputs


@pytest.mark.asyncio
async def test_task_timeout_cancels_stalled_model(monkeypatch):
    original = ConfigManager.get
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None:
        0.02 if key == 'orchestration.browser_task_timeout_seconds' else original(key, default))
    class StalledModel:
        def bind_tools(self, schemas):
            return self
        async def ainvoke(self, messages, config=None):
            await asyncio.Event().wait()
    graph = build_specialist_graph(StalledModel(), [browser_snapshot], '', name='browser_agent')
    result = await asyncio.wait_for(graph.ainvoke({'messages': [HumanMessage(content='Read page')]}), 2)
    assert result['browser_progress']['status'] == 'incomplete'
    assert 'timed out' in result['browser_progress']['reason']


@pytest.mark.asyncio
async def test_task_timeout_cancels_stalled_tool_and_reports_unverified_outcome(monkeypatch):
    original = ConfigManager.get
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None:
        0.1 if key == 'orchestration.browser_task_timeout_seconds' else original(key, default))
    cancelled = asyncio.Event()
    @tool
    async def browser_click(target: str) -> dict:
        """Click an observed target."""
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    model = ScriptedModel([decision('browser_click', {'target': 'e1'})])
    graph = build_specialist_graph(model, [browser_click], '', name='browser_agent')
    result = await asyncio.wait_for(graph.ainvoke({'messages': [HumanMessage(content='Click target')]}), 2)
    assert result['browser_progress']['status'] == 'incomplete'
    assert 'unverified' in result['messages'][-1].content
    assert cancelled.is_set() and len(model.inputs) == 1
