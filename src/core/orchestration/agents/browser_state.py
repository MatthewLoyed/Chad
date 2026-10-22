"""Browser-task state and deterministic observation/progress checks."""
import hashlib
import json
import re
import time
import uuid

from src.shared.captured_content import load_captured_text
from src.tools.web.browser_refs import REF_PATTERN


def delegation_input(task, delegation_id, prior=None):
    resume = (prior and prior.get('status') == 'awaiting_approval'
              and prior.get('contract', {}).get('objective') == task
              and prior.get('contract', {}).get('delegation_id') == delegation_id)
    return {'browser_task': {'objective': task, 'delegation_id': delegation_id},
            'browser_progress': prior if resume else None,
            'browser_budget_exhausted': False}


def payload(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        result = json.loads(value.removeprefix('[TOOL_DATA]\n'))
        return result if isinstance(result, dict) else {}
    except ValueError:
        return {}


def result_text(value):
    data = payload(value)
    result = data.get('result', value)
    if isinstance(result, list):
        return '\n'.join(str(p.get('text', '')) for p in result if isinstance(p, dict))
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def normalize_observation(text):
    text = re.sub(r'\b(?:target=)?["\']?' + REF_PATTERN + r'["\']?', '<ref>', text)
    text = re.sub(r'\d{1,2}:\d{2}(?::\d{2})?', '<time>', text)
    return re.sub(r'\s+', ' ', text).strip()


def fingerprint(text):
    return hashlib.sha256(normalize_observation(text).encode()).hexdigest()[:20]


def action_signature(name, arguments, observation):
    args = dict(arguments)
    target = args.get('target')
    if target:
        label = next((line for line in observation.splitlines() if f'[ref={target}]' in line or f'target="{target}"' in line), target)
        args['target'] = normalize_observation(label)
    return name + ':' + json.dumps(args, sort_keys=True, ensure_ascii=False)


def new_progress(objective, contract=None):
    contract = dict(contract or {})
    contract.setdefault('objective', objective)
    contract.setdefault('milestones', ['Locate the requested target', 'Perform the requested browser operation', 'Verify the deliverable'])
    exact_text = re.search(r'verbatim|(?:capture|copy).*(?:text|message|response|sermon)', objective, re.I)
    contract.setdefault('deliverable', 'content_id' if exact_text else 'observed_result')
    contract.setdefault('constraints', 'Only the requested browser work; use native tools for other operations.')
    return {'run_id': uuid.uuid4().hex, 'contract': contract, 'started_at': time.time(),
            'actions': 0, 'stalls': 0, 'recoveries': 0, 'invalid_decisions': 0,
            'milestone': contract['milestones'][0], 'verified_milestones': [],
            'memory': '', 'observation': '', 'last_result': '', 'recent': [],
            'seen': [], 'content_refs': [], 'status': 'running', 'reason': '',
            'peak_context_chars': 0, 'unproductive_actions': 0, 'last_error': ''}


def observe(progress, tool_name, raw, decision, stall_limit=3, max_recoveries=2):
    """A tool success, a novel page, and goal completion are different facts."""
    p = dict(progress)
    p['actions'] += 1
    data, text = payload(raw), result_text(raw)
    good = data.get('success') is not False and not data.get('failure_class')
    if data.get('_approval_declined'):
        return dict(p, status='blocked', reason='The requested browser action was declined.')
    current = fingerprint(text)
    is_page = ('Page URL:' in text or '### Pruned Snapshot' in text or tool_name == 'browser_snapshot')
    # Search snippets and metadata do not replace the last complete observation.
    if is_page and good:
        p['observation'] = text
    novel = bool(good and current not in p['seen'] and normalize_observation(text))
    # Repeated waits/snapshots do not get credit for transport banners changing.
    captured = False
    if tool_name == 'browser_capture_text' and data.get('success') is True:
        try:
            record = load_captured_text(data.get('content_id'))
            p['content_refs'] = list(dict.fromkeys(p['content_refs'] + [record['content_id']]))
            captured = True
        except ValueError:
            good = False
    relevant_change = novel and tool_name not in ('browser_wait_for',)
    # A snapshot is evidence only when it reveals a new page, not repeated refs.
    progress_made = bool(captured or relevant_change)
    scroll = data.get('scroll') or {}
    if scroll:
        p['at_bottom'] = bool(scroll.get('at_bottom'))
    elif tool_name == 'browser_navigate':
        p['at_bottom'] = False
    if not good:
        p['memory'] = ''
    # Traversing a long paragraph can legitimately move the viewport without
    # changing accessibility text. Allow bounded, monotonic exploration; neither
    # geometry alone nor returning to a previously visited position is success.
    position = f"{scroll.get('container')}:{round(float(scroll.get('top', 0)))}"
    positions = p.get('scroll_positions', [])
    exploring = (tool_name == 'browser_scroll' and scroll.get('moved') is True
                 and position not in positions and not (scroll.get('at_top') or scroll.get('at_bottom')))
    p['scroll_exploration'] = p.get('scroll_exploration', 0) + 1 if exploring and not progress_made else 0
    exploring = exploring and p['scroll_exploration'] <= 3
    p['scroll_positions'] = (positions + ([position] if scroll else []))[-40:]
    p['seen'] = (p['seen'] + [current])[-40:]
    p['stalls'] = 0 if progress_made else p['stalls'] + int(not exploring)
    p['unproductive_actions'] += int(not progress_made)
    p['last_result'] = text[-4000:] + ('\nSCROLL CONTAINER STATE: ' + json.dumps(scroll) if scroll else '')
    p['last_error'] = '' if good else str(data.get('error') or text)[:600]
    p['memory'] = ''  # Current observations, not model-authored claims, drive the next action.
    p['milestone'] = decision.get('milestone', p['milestone'])[:240]
    args = decision.get('arguments', {})
    compact_args = {k: (v[:240] if isinstance(v, str) else v) for k, v in args.items()}
    p['recent'] = (p['recent'] + [{'action': tool_name, 'args': compact_args,
        'signature': action_signature(tool_name, args, progress['observation']),
        'goal': decision.get('next_goal', ''), 'expected': decision.get('expected_change', ''),
        'tool_success': good, 'observation_changed': novel, 'capture_verified': captured}])[-6:]
    # Model milestone claims stay unverified. Only machine-checked artifacts can
    # promote a milestone here; other evidence is checked at the terminal gate.
    if captured:
        p['verified_milestones'] = list(dict.fromkeys(p['verified_milestones'] + ['Full text captured']))
    if p['stalls'] >= stall_limit:
        p['stalls'] = 0
        p['recoveries'] += 1
        p['reason'] = 'No useful new evidence. Change strategy: inspect/search, change the scroll container, or resolve an observed blocker. Another snapshot alone is not recovery.'
        if p['recoveries'] > max_recoveries:
            p.update(status='incomplete', reason='Stopped after repeated actions without useful new evidence.')
    elif progress_made:
        p['reason'] = ''
    return p


def finish(progress, arguments):
    status = arguments.get('status')
    summary = arguments.get('summary', '')
    evidence = arguments.get('evidence', '')
    if status not in ('completed', 'blocked', 'incomplete') or not isinstance(summary, str) or not summary.strip():
        raise ValueError('Finish requires status completed/blocked/incomplete and a factual summary.')
    if status == 'completed':
        if progress['contract']['deliverable'] == 'content_id':
            content_id = arguments.get('content_id')
            if content_id not in progress['content_refs']:
                raise ValueError('Completion requires a content_id verified during THIS task.')
            load_captured_text(content_id)
        else:
            if not isinstance(evidence, str) or len(evidence.strip()) < 3:
                raise ValueError('Completion requires an exact quote from the current observation.')
            if normalize_observation(evidence) not in normalize_observation(progress['observation']):
                raise ValueError('Completion evidence is absent from the current page observation.')
            if progress['last_error']:
                raise ValueError('An unresolved tool error cannot establish completion; inspect and verify first.')
    p = dict(progress, status=status, reason=summary[:1500])
    if status == 'completed':
        p['next_goal'] = ''
        if p['contract']['deliverable'] == 'content_id':
            p['milestone'] = 'Full text captured'
    p['terminal_evidence'] = evidence[:1500] if isinstance(evidence, str) else ''
    return p


def terminal_message(p):
    return json.dumps({'browser_status': p['status'], 'summary': p['reason'],
        'verified_milestones': p['verified_milestones'], 'content_ids': p['content_refs'],
        'evidence': p.get('terminal_evidence', ''), 'actions': p['actions'],
        'recoveries': p['recoveries']}, ensure_ascii=False)
