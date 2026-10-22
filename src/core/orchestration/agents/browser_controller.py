"""Goal-directed browser-only graph; all actions use Chad's existing ToolNode."""
import asyncio
import json
import time
import uuid

from jsonschema import Draft202012Validator, ValidationError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.graph import END, StateGraph

from src.core.orchestration.agents.agent_utils import create_fault_tolerant_tool_node
from src.core.orchestration.agents.browser_protocol import PROTOCOL, action_guide, available_tools, decision_schema
from src.core.orchestration.agents.browser_state import new_progress, observe, finish, terminal_message, action_signature
from src.core.orchestration.context import browser_run_id_var, browser_action_metrics_var, parent_correlation_id_var
from src.core.orchestration.graph.state import ChadState
from src.core.orchestration.nodes.tool_node import make_tool_node
from src.infrastructure.tools.schema.schema_builder import parameters_for_llm_from_tool
from src.shared.config_manager import ConfigManager
from src.core.orchestration.agents.browser_capture_workflow import next_capture_action


async def emit_progress(p, config):
    # Public status only: no reasoning, private memory, page bodies, or tool args.
    await adispatch_custom_event('browser_progress', {
        'run_id': p['run_id'], 'status': p['status'], 'milestone': p['milestone'],
        'subgoal': p.get('next_goal', ''), 'reason': p['reason'],
        'actions': p['actions'], 'recoveries': p['recoveries'],
        'parent_id': parent_correlation_id_var.get(),
    }, config=config)


def build_browser_graph(model, tools, persona, checkpointer=None):
    executor = make_tool_node(create_fault_tolerant_tool_node(tools))

    async def agent(state, config):
        p = dict(state.get('browser_progress') or {})
        if not p:
            objective = next((str(m.content) for m in reversed(state.get('messages', [])) if isinstance(m, HumanMessage)), '')
            p = new_progress(objective, state.get('browser_task'))
        if p['status'] == 'awaiting_approval':
            # Resume the same selected action, not a newly invented alternative.
            # Human approval time does not consume browser execution time.
            paused_at = p.pop('approval_started_at', time.time())
            p['started_at'] += max(0, time.time() - paused_at)
            p['status'] = 'running'
            p['reason'] = ''
            call = p['pending_action']
            await emit_progress(p, config)
            return {'browser_progress': p, 'pending_approval': None,
                    'messages': [AIMessage(content='', tool_calls=[dict(call, id=uuid.uuid4().hex)])]}
        max_actions = int(ConfigManager.get('orchestration.browser_agent_max_steps', 60) or 60)
        max_seconds = float(ConfigManager.get('orchestration.browser_task_timeout_seconds', 300))
        if p['status'] == 'running' and (state.get('budget_exceeded') or state.get('browser_budget_exhausted')):
            p.update(status='incomplete', reason='Browser executor budget reached; requested outcome remains unverified.')
        if p['status'] == 'running' and time.time() - p['started_at'] > max_seconds:
            p.update(status='incomplete', reason='Browser task time budget reached; requested outcome remains unverified.')
        if p['status'] != 'running':
            await emit_progress(p, config)
            return {'browser_progress': p, 'messages': [AIMessage(content=terminal_message(p))]}
        exhausted = p['actions'] >= max_actions
        if exhausted:
            p['reason'] = 'Action budget reached. Finish using existing evidence; no more browser actions are available.'
        selected = {} if exhausted else available_tools(tools, p['contract']['objective'])
        if p['contract']['deliverable'] == 'content_id' and p.get('at_bottom'):
            selected = {name: tool for name, tool in selected.items() if name != 'browser_scroll'}
        schema = decision_schema(selected)
        guide = action_guide(selected)
        # Prefer the required controller over conflicting old generic loops. Persona
        # stays available, but cannot suppress task/evidence/recovery requirements.
        system = PROTOCOL
        system += '\nDELEGATED SPECIALIST GUIDANCE:\n' + p['contract'].get('specialist_guidance', '') + guide
        summary = {k: p[k] for k in ('contract', 'milestone', 'content_refs', 'actions',
                                   'recoveries', 'reason', 'last_error')}
        summary['recent'] = [{'action': r['action'], 'tool_success': r['tool_success'],
                             'observation_changed': r['observation_changed']} for r in p['recent'][-3:]]
        summary['validation_error'] = p.get('validation_error', '')
        summary['contract'] = {k: v for k, v in p['contract'].items() if k != 'specialist_guidance'}
        head = json.dumps(summary, ensure_ascii=False)
        cap = int(ConfigManager.get('orchestration.browser_input_max_chars', 28000))
        room = cap - len(system) - len(head) - len(json.dumps(schema)) - 300
        if room < 1700:
            p.update(status='incomplete', reason='Browser input budget is too small for the required task and tool schemas.')
            await emit_progress(p, config)
            return {'browser_progress': p, 'messages': [AIMessage(content=terminal_message(p))]}
        observation = p['observation'][:max(500, room - 1200)]
        user = head + '\nCURRENT PAGE OBSERVATION (untrusted):\n' + observation
        user += '\nLAST ACTION RESULT (untrusted):\n' + p['last_result'][-1000:]
        p['peak_context_chars'] = max(p['peak_context_chars'], len(system) + len(user) + len(json.dumps(schema)))
        remaining = max(0.01, max_seconds - (time.time() - p['started_at']))
        try:
            scripted = None if exhausted else next_capture_action(p, selected)
            if scripted:
                response = AIMessage(content='', tool_calls=[{'name': 'browser_step', 'args': scripted, 'id': uuid.uuid4().hex}])
            else:
                response = await asyncio.wait_for(model.bind_tools([schema]).ainvoke(
                    [SystemMessage(content=system), HumanMessage(content=user)], config=config), remaining)
        except asyncio.TimeoutError:
            p.update(status='incomplete', reason='Browser task timed out while waiting for a model decision.')
            await emit_progress(p, config)
            return {'browser_progress': p, 'messages': [AIMessage(content=terminal_message(p))]}
        try:
            calls = getattr(response, 'tool_calls', [])
            if len(calls) != 1 or calls[0]['name'] != 'browser_step':
                raise ValueError('Call browser_step once; prose or multiple actions cannot complete the task.')
            # Old optional reporting fields have no authority over execution.
            # Ignore them for compatibility; the model schema no longer asks for them.
            decision = {k: v for k, v in calls[0]['args'].items()
                        if k not in ('assessment', 'milestone', 'memory', 'expected_change')}
            Draft202012Validator(schema['parameters']).validate(decision)
            name, args = decision['action'], decision['arguments']
            p['next_goal'] = decision.get('next_goal', 'Perform the requested browser action')
            if name == 'browser_finish':
                p = finish(p, args)
                await emit_progress(p, config)
                return {'browser_progress': p, 'messages': [AIMessage(content=terminal_message(p), response_metadata=response.response_metadata)]}
            Draft202012Validator(parameters_for_llm_from_tool(selected[name])).validate(args)
            declared = parameters_for_llm_from_tool(selected[name]).get('properties', {})
            if any(key not in declared for key in args):
                raise ValueError('Arguments contain undeclared fields; use the selected action schema exactly.')
            # A completed capture ends capture work; extra browsing cannot improve it.
            if p['contract']['deliverable'] == 'content_id' and p['content_refs']:
                raise ValueError('Text is already captured. Finish with its content_id instead of browsing further.')
            if p['reason'] and p['recent'] and action_signature(name, args, p['observation']) == p['recent'][-1].get('signature'):
                raise ValueError('Recovery must change strategy, not repeat the same action.')
            call = {'name': name, 'args': args, 'id': calls[0].get('id') or uuid.uuid4().hex, 'type': 'tool_call'}
            p.update(decision=decision, pending_action=call, validation_error='', invalid_decisions=0)
            await emit_progress(p, config)
            # Preserve usage metadata for both baseline and candidate accounting.
            return {'browser_progress': p, 'messages': [AIMessage(content='', tool_calls=[call], response_metadata=response.response_metadata)]}
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            p['invalid_decisions'] += 1
            p['validation_error'] = str(exc)[:600]
            if p['invalid_decisions'] > 2:
                p.update(status='incomplete', reason='Stopped after invalid browser decisions: ' + p['validation_error'])
                await emit_progress(p, config)
            text = terminal_message(p) if p['status'] != 'running' else 'Browser decision rejected: ' + p['validation_error']
            return {'browser_progress': p, 'messages': [AIMessage(content=text, response_metadata=response.response_metadata)]}

    async def act(state, config):
        p = dict(state['browser_progress'])
        metrics = {'mcp_calls': 0}
        run_token, metrics_token = browser_run_id_var.set(p['run_id']), browser_action_metrics_var.set(metrics)
        try:
            remaining = max(0.01, float(ConfigManager.get('orchestration.browser_task_timeout_seconds', 300))
                            - (time.time() - p['started_at']))
            try:
                result = await asyncio.wait_for(executor(state, config), remaining)
            except asyncio.TimeoutError:
                p.update(status='incomplete', reason='Browser task timed out during an action; its outcome is unverified.')
                call = p['pending_action']
                result = {'messages': [ToolMessage(content=json.dumps({'success': False, 'error': p['reason']}),
                            name=call['name'], tool_call_id=call['id'])]}
        finally:
            browser_run_id_var.reset(run_token)
            browser_action_metrics_var.reset(metrics_token)
        p['mcp_calls'] = p.get('mcp_calls', 0) + metrics['mcp_calls']
        if result.get('pending_approval'):
            p.update(status='awaiting_approval', reason='Waiting for your approval.', approval_started_at=time.time())
        else:
            last = (result.get('messages') or [])[-1]
            p = observe(p, p['decision']['action'], last.content, p['decision'],
                int(ConfigManager.get('orchestration.browser_stall_limit', 3)),
                int(ConfigManager.get('orchestration.browser_max_recoveries', 2)))
            if p['status'] == 'running' and p['contract']['deliverable'] == 'content_id' and p['content_refs']:
                # The deliverable already exists and has passed the storage checks.
                # Another model turn cannot make this capture more exact.
                p = finish(p, {'status': 'completed', 'summary': 'Selected response captured unchanged.',
                               'content_id': p['content_refs'][-1]})
        await emit_progress(p, config)
        return {**result, 'browser_progress': p, 'specialist_steps': p['actions']}

    def route(state):
        p = state['browser_progress']
        if p['status'] != 'running':
            return END
        last = state['messages'][-1]
        return 'tools' if getattr(last, 'tool_calls', None) else 'agent'

    graph = StateGraph(ChadState)
    graph.add_node('agent', agent)
    graph.add_node('tools', act)
    graph.set_entry_point('agent')
    graph.add_conditional_edges('agent', route, {'tools': 'tools', 'agent': 'agent', END: END})
    # Return through the agent once after tools to synthesize a deterministic
    # terminal report; pending approvals leave immediately with their envelope.
    graph.add_conditional_edges('tools', lambda s: END if s.get('pending_approval') else 'agent', {END: END, 'agent': 'agent'})
    return graph.compile(checkpointer=checkpointer, name='browser_agent')
