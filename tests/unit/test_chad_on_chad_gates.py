"""Fail-closed cycle gates: no model, Docker or credentials required."""
import json
from unittest.mock import AsyncMock, Mock
import pytest
from evals import chad_on_chad_driver as d
from src.tools.dev import eval_clone_provisioner as provision

POLICY = {'paths':['src/output.py'], 'acceptance_check_ids':['answer'], 'regression_check_ids':['nearby'], 'failure_signals':[{'kind':'response_not_json'}], 'checks':{'answer':'Replay exact answer contract','nearby':'Run nearby regression contracts'}}
TRIAGE = {'schema_version':1,'observations':[{'id':'o1','kind':'response_not_json'}], 'hypotheses':[{'text':'Possible output-format issue','observation_ids':['o1']}], 'recommendation':'repair','repair':{key:POLICY[key] for key in ('paths','acceptance_check_ids','regression_check_ids')}}

@pytest.fixture
def cycle(monkeypatch,tmp_path):
    monkeypatch.setattr(d,'load_candidate',lambda _: {'prompt':'repeat exactly','why':'regression','triage_policy':POLICY})
    monkeypatch.setattr(d.subprocess,'check_output',Mock(side_effect=['sha','','sha','']))
    before=AsyncMock(return_value={'response':'bad','error':None})
    diagnosis=AsyncMock(return_value={'response':json.dumps(TRIAGE)})
    dispatch=AsyncMock(return_value={'success':True,'result':{'job_id':'job'}})
    poll=AsyncMock(return_value={'success':True,'result':{'status':'finished','exit_code':'0'}})
    after=Mock(return_value={'response':'good'})
    judge=AsyncMock(return_value={'response':'IMPROVED'})
    for name,value in [('run_before',before),('diagnose',diagnosis),('dispatch_fix',dispatch),('poll_job',poll),('run_after',after),('judge',judge)]: monkeypatch.setattr(d,name,value)
    clone={'success':True,'path':str(tmp_path),'secrets_env':{'PRIVATE':'secret'},'allowlist_env':{'GRANT':'private'}}
    monkeypatch.setattr(provision,'provision_eval_clone',Mock(return_value=clone))
    cleanup=Mock(); monkeypatch.setattr(provision,'deprovision_eval_clone',cleanup)
    capture=Mock(return_value={'changed':True,'diff':'patch','sha256':'hash'})
    monkeypatch.setattr(d,'capture_patch',capture)
    save=Mock(); monkeypatch.setattr(d,'_save_report',save)
    return dict(before=before,diagnosis=diagnosis,dispatch=dispatch,poll=poll,after=after,judge=judge,cleanup=cleanup,capture=capture,save=save)

@pytest.mark.parametrize('job',[{'success':False}, {'success':True,'result':{'status':'failed','exit_code':'1'}}, {'success':True,'timed_out':True,'result':{'status':'running'}}])
async def test_failed_workers_never_replay(cycle,job):
    cycle['poll'].return_value=job
    r=await d.run_cycle('case')
    cycle['after'].assert_not_called(); cycle['judge'].assert_not_awaited(); cycle['save'].assert_called_once()
    assert r['decision']!='ready_for_review'
    if job.get('timed_out'): cycle['cleanup'].assert_not_called(); assert r['clone_retained']

async def test_no_op_never_replays(cycle):
    cycle['capture'].return_value={'changed':False}
    r=await d.run_cycle('case'); assert r['decision']=='needs_work'; cycle['after'].assert_not_called()

async def test_model_praise_alone_is_inconclusive_and_no_secret_env_persisted(cycle):
    r=await d.run_cycle('case'); assert r['decision']=='inconclusive'
    assert 'secrets_env' not in r['clone'] and 'allowlist_env' not in r['clone']
    assert r['patch']['diff']=='patch'; cycle['cleanup'].assert_called_once()

@pytest.mark.parametrize('verification,expected',[
    ({'baseline_passed':False,'after_passed':True,'regressions_passed':True,'passed':True},'ready_for_review'),
    ({'baseline_passed':True,'after_passed':True,'regressions_passed':True,'passed':True},'inconclusive'),
    ({'after_passed':True,'passed':True},'inconclusive'),
    ({'baseline_passed':False,'after_passed':True,'regressions_passed':False,'passed':False},'needs_work')])
async def test_independent_gate_is_required(cycle,verification,expected):
    r=await d.run_cycle('case',verifier=lambda *args:verification); assert r['decision']==expected

async def test_baseline_error_skips_diagnosis_and_saves(cycle):
    cycle['before'].return_value={'error':'model unavailable'}
    r=await d.run_cycle('case'); assert r['decision']=='inconclusive'; cycle['diagnosis'].assert_not_awaited(); cycle['save'].assert_called_once()

async def test_exception_saves_report_and_keeps_possibly_active_worker(cycle):
    cycle['poll'].side_effect=RuntimeError('poll error')
    r=await d.run_cycle('case'); assert 'poll error' in r['error']; cycle['cleanup'].assert_not_called(); cycle['save'].assert_called_once()

async def test_cleanup_error_does_not_lose_report(cycle):
    cycle['cleanup'].side_effect=RuntimeError('cleanup failed')
    r=await d.run_cycle('case'); assert 'cleanup_error' in r; cycle['save'].assert_called_once()

async def test_replay_error_skips_judge(cycle):
    cycle['after'].return_value={'error':'import failed'}
    r=await d.run_cycle('case'); assert r['decision']=='inconclusive'; cycle['judge'].assert_not_awaited()

async def test_dirty_baseline_blocks_expensive_calls(cycle,monkeypatch):
    monkeypatch.setattr(d.subprocess,'check_output',Mock(side_effect=['sha',' M source.py']))
    r=await d.run_cycle('case'); assert 'clean checkout' in r['error']; cycle['before'].assert_not_awaited()

def test_real_patch_capture_preserves_tracked_and_new_files(tmp_path):
    import base64
    import shutil
    import subprocess
    git=shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'
    def run(*args):
        return subprocess.run([git,*args],cwd=tmp_path,check=True,capture_output=True)
    run('init')
    (tmp_path/'source.py').write_text('VALUE=0\n')
    run('add','source.py')
    run('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','baseline')
    (tmp_path/'source.py').write_text('VALUE=1\n')
    (tmp_path/'new.py').write_text('new evidence\n')
    result=d.capture_patch(tmp_path)
    assert result['changed'] and '+VALUE=1' in result['diff']
    assert base64.b64decode(result['new_files_base64']['new.py'])==(tmp_path/'new.py').read_bytes()
    assert len(result['sha256'])==64

async def test_registered_contract_skips_repair_when_baseline_passes(cycle,monkeypatch):
    import json
    from evals.chad_behavior_contracts import CASES,CONTRACT_ID
    monkeypatch.setattr(d,'load_candidate',lambda _: {'prompt':CASES['sum']['prompt'],'acceptance_contract':CONTRACT_ID,'triage_policy':POLICY})
    cycle['before'].return_value={'response':json.dumps(CASES['sum']['expected'])}
    r=await d.run_cycle('structured-answer-local')
    assert r['decision']=='already_passes'
    cycle['diagnosis'].assert_not_awaited(); cycle['dispatch'].assert_not_awaited()

async def test_registered_contract_runs_matched_replay_and_regressions(cycle,monkeypatch):
    import json
    from evals.chad_behavior_contracts import CASES,CONTRACT_ID
    monkeypatch.setattr(d,'load_candidate',lambda _: {'prompt':CASES['sum']['prompt'],'acceptance_contract':CONTRACT_ID,'triage_policy':POLICY})
    cycle['before'].side_effect=[{'response':'wrong'}]+[{'response':json.dumps(CASES[k]['expected'])} for k in ('extract','unknown')]
    cycle['after'].side_effect=[{'response':json.dumps(CASES[k]['expected'])} for k in ('sum','extract','unknown')]
    r=await d.run_cycle('structured-answer-local')
    assert r['decision']=='ready_for_review'
    assert r['verification']['passed']
    assert [c.args[0] for c in cycle['before'].call_args_list]==[c.args[0] for c in cycle['after'].call_args_list]
    assert len(r['baseline_regressions'])==len(r['patched_regressions'])==2

async def test_failed_baseline_regression_blocks_coder(cycle,monkeypatch):
    from evals.chad_behavior_contracts import CASES,CONTRACT_ID
    monkeypatch.setattr(d,'load_candidate',lambda _: {'prompt':CASES['sum']['prompt'],'acceptance_contract':CONTRACT_ID,'triage_policy':POLICY})
    cycle['before'].side_effect=[{'response':'wrong'},{'error':'model unavailable'}]
    r=await d.run_cycle('structured-answer-local')
    assert r['decision']=='inconclusive'; cycle['dispatch'].assert_not_awaited()

async def test_unverified_legacy_diagnosis_never_provisions_or_dispatches(cycle):
    cycle['diagnosis'].return_value={'response':'High confidence: no file reads occurred; change the workflow.'}
    r=await d.run_cycle('case')
    assert r['stage']=='triage_gate' and r['decision']=='inconclusive'
    assert not r['triage_gate']['dispatch_allowed']
    cycle['dispatch'].assert_not_awaited(); cycle['after'].assert_not_called(); cycle['save'].assert_called_once()

async def test_structured_contradiction_never_dispatches(cycle):
    import copy
    bad=copy.deepcopy(TRIAGE)
    bad['observations']=[{'id':'o1','kind':'response_equals','value':'fabricated output'}]
    cycle['diagnosis'].return_value={'response':json.dumps(bad)}
    r=await d.run_cycle('case')
    assert not r['triage_gate']['valid']; cycle['dispatch'].assert_not_awaited()

async def test_valid_gate_hands_off_verified_brief_not_legacy_prose(cycle):
    await d.run_cycle('case')
    brief=cycle['dispatch'].call_args.args[0]
    assert 'UNVERIFIED' in brief and 'operator_policy' in brief
    assert 'Reproduce before editing' in brief


async def test_persona_policy_requires_explicit_capture_ids(cycle, monkeypatch):
    monkeypatch.setattr(d, 'load_candidate', lambda _: {'prompt': 'test', 'triage_policy': {
        **POLICY, 'paths': ['config/persona/routing.md']}})
    result = await d.run_cycle('case')
    assert 'persona_capture_ids' in result['error']
    cycle['before'].assert_not_awaited()
    cycle['dispatch'].assert_not_awaited()


@pytest.fixture
def persona_cycle(cycle, tmp_path, monkeypatch):
    monkeypatch.delenv('CHAD_PERSONA_LAYER', raising=False)
    monkeypatch.delenv('CHAD_PERSONA_OVERLAY_DIR', raising=False)
    for prefix in ('config/persona', 'prompts/persona'):
        (tmp_path / prefix).mkdir(parents=True)
    (tmp_path / 'config/persona/routing.md').write_text('Custom routing')
    (tmp_path / 'prompts/persona/routing.md').write_text('Generic routing')
    monkeypatch.setattr(d, '_repo_root', lambda: tmp_path)
    return cycle


async def test_persona_drift_during_diagnosis_blocks_dispatch(persona_cycle, tmp_path):
    async def diagnose(*args):
        (tmp_path / 'config/persona/routing.md').write_text('Unexpected source drift')
        return {'response': json.dumps(TRIAGE)}
    persona_cycle['diagnosis'].side_effect = diagnose
    result = await d.run_cycle('case', persona_capture_ids=('routing',))
    assert 'drifted' in result['error']
    persona_cycle['dispatch'].assert_not_awaited()


async def test_persona_clone_mismatch_blocks_dispatch(persona_cycle, monkeypatch):
    monkeypatch.setattr(d, 'require_matching_persona', Mock(side_effect=[None, ValueError('Clone persona mismatch')]))
    result = await d.run_cycle('case', persona_capture_ids=('routing',))
    assert 'Clone persona mismatch' in result['error']
    persona_cycle['dispatch'].assert_not_awaited()


async def test_persona_capture_error_retains_clone_and_report(persona_cycle):
    persona_cycle['capture'].side_effect = ValueError('Unallowlisted persona overlay changed')
    result = await d.run_cycle('case', persona_capture_ids=('routing',))
    assert result['decision'] == 'inconclusive'
    assert result['clone_retained'] and 'patch_capture_error' in result
    persona_cycle['after'].assert_not_called()
    persona_cycle['cleanup'].assert_not_called()
    persona_cycle['save'].assert_called_once()


async def test_persona_baseline_and_capture_are_persisted(persona_cycle):
    result = await d.run_cycle('case', persona_capture_ids=('routing',))
    snapshot = result['persona_baseline']
    assert snapshot['fragments']['routing']['effective_path'] == 'config/persona/routing.md'
    assert persona_cycle['capture'].call_args.kwargs['persona_snapshot'] == snapshot
