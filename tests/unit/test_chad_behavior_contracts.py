import json
import pytest
from evals.chad_behavior_contracts import evaluate,summarize,CASES
from evals import chad_on_chad_driver as d

def trace(key): return {'response':json.dumps(CASES[key]['expected'])}
@pytest.mark.parametrize('response',['{"count":true,"total":54}','{"count":3,"total":55}','```json\n{"count":3,"total":54}\n```','{"count":3,"total":54,"extra":1}','{"count":9,"count":3,"total":54}'])
def test_bad_answers_do_not_pass(response): assert evaluate('sum',{'response':response})['passed'] is False

def test_exact_answer_passes(): assert evaluate('sum',trace('sum'))['passed'] is True

def test_infrastructure_error_is_not_behavior_failure(): assert evaluate('sum',{'error':'model unavailable'})['passed'] is None

def test_regression_or_baseline_success_prevents_readiness():
    regressions={key:trace(key) for key in ('extract','unknown')}
    r=summarize({'response':'wrong'},trace('sum'),regressions,regressions); assert r['passed']
    assert not summarize(trace('sum'),trace('sum'),regressions,regressions)['passed']
    bad={**regressions,'unknown':{'response':'{"color":"red","count":9}'}}
    assert not summarize({'response':'wrong'},trace('sum'),regressions,bad)['passed']
