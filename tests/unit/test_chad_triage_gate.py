import copy
import json
import pytest
from evals.chad_triage_gate import validate_triage,coding_brief

POLICY={'paths':['prompts/persona/rich_output.md'],'acceptance_check_ids':['raw-json'],'regression_check_ids':['normal-web','normal-code'],'failure_signals':[{'kind':'tool_call','name':'web_search'}], 'checks':{'raw-json':'Require exact raw JSON and no recorded network attempts','normal-web':'Explicit research request must still use web tools','normal-code':'Explicit code generation request must still route to coding'}}
TRACE={'response':'{"files":["a.txt"]}','tool_calls':[{'name':'list_directory','params':{'path':'fixture'}},{'name':'web_search','params':{'query':'irrelevant'}}]}

def proposal():
    return {'schema_version':1,'observations':[{'id':'o1','kind':'tool_call','index':1,'name':'web_search'}], 'hypotheses':[{'text':'Scope instruction may have been overridden','observation_ids':['o1']}], 'recommendation':'repair','repair':{key:POLICY[key][:] for key in ('paths','acceptance_check_ids','regression_check_ids')}}

def gate(value,trace=TRACE,policy=POLICY): return validate_triage(json.dumps(value),trace,policy)

def test_supported_evidence_and_full_policy_allow_bounded_brief():
    r=gate(proposal()); assert r['dispatch_allowed'] and r['evidence']['hypotheses_verified'] is False
    brief=coding_brief(r,POLICY); assert 'UNVERIFIED' in brief and 'web_search' in brief

@pytest.mark.parametrize('change',[
    lambda p:p['observations'][0].update(index=0),
    lambda p:p['observations'][0].update(index=99),
    lambda p:p['observations'][0].update(index=True),
    lambda p:p['observations'][0].update(kind='tool_absent'),
    lambda p:p['observations'][0].update(claim='different workflows'),
    lambda p:p['hypotheses'][0].update(observation_ids=['invented']),
    lambda p:p['repair'].update(paths=['../config/secrets.yaml']),
    lambda p:p['repair'].update(paths=['src/unapproved.py']),
    lambda p:p['repair'].update(regression_check_ids=['normal-web']),
    lambda p:p.update(confidence=0.99),
])
def test_contradictions_unapproved_scope_and_missing_checks_block(change):
    p=proposal(); change(p); assert not gate(p)['dispatch_allowed']

@pytest.mark.parametrize('text',['```json\n{}\n```','not JSON','{"schema_version":1,"schema_version":1}'])
def test_non_schema_outputs_fail_closed(text):
    assert not validate_triage(text,TRACE,POLICY)['dispatch_allowed']

def test_legacy_diagnosis_cannot_bypass_gate():
    for text in ('The trace proves a root cause. Confidence high.',json.dumps({'observed_failures':['No file reads occurred'],'suspected_cause':'different workflows','confidence':0.85})):
        assert not validate_triage(text,TRACE,POLICY)['dispatch_allowed']

def test_infrastructure_and_missing_policy_never_authorize():
    assert not gate(proposal(),policy={})['dispatch_allowed']
    assert not gate(proposal(),trace={**TRACE,'error':'timeout'})['dispatch_allowed']

def test_true_but_irrelevant_fact_cannot_authorize_repair():
    p=proposal(); p['observations'][0].update(index=0,name='list_directory')
    assert not gate(p)['dispatch_allowed']

def test_false_non_json_claim_is_rejected():
    p=proposal(); p['observations']=[{'id':'o1','kind':'response_not_json'}]
    assert not gate(p)['dispatch_allowed']

def test_investigation_is_valid_but_not_dispatchable():
    p=proposal();p['recommendation']='investigate';r=gate(p)
    assert r['valid'] and not r['dispatch_allowed']
    with pytest.raises(ValueError): coding_brief(r,POLICY)


def test_undefined_operator_check_blocks_dispatch():
    policy=copy.deepcopy(POLICY);policy.pop('checks')
    assert not gate(proposal(),policy=policy)['dispatch_allowed']
