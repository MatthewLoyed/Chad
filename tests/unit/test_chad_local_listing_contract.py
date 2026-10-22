from pathlib import Path
from evals.chad_local_listing_contract import evaluate_listing


def test_correct_answer_cannot_hide_forbidden_network_call():
    folder = Path('fixture')
    trace = {'status':'success','final_response':'{"files":["a.txt"]}',
             'tool_calls':[{'name':'list_directory','params':{'path':str(folder)}},{'name':'web_search','params':{'query':'unnecessary'}}]}
    result = evaluate_listing(trace,folder,['a.txt'],True)
    assert result['correct_raw_json'] and not result['passed']
    assert len(result['network_attempts'])==1


def test_correct_local_listing_passes_but_markdown_does_not():
    trace = {'status':'success','final_response':'{"files":["a.txt"]}',
             'tool_calls':[{'name':'list_directory','params':{'path':'fixture'}}]}
    assert evaluate_listing(trace,Path('fixture'),['a.txt'],True)['passed']
    trace['final_response']='```json\n{"files":["a.txt"]}\n```'
    result=evaluate_listing(trace,Path('fixture'),['a.txt'],True)
    assert result['local_listing_recorded'] and not result['passed']


def test_infrastructure_failure_remains_inconclusive():
    assert evaluate_listing({'status':'failure','error':'timeout'},Path('fixture'),[],True)['passed'] is None


def test_duplicate_json_keys_cannot_hide_an_earlier_wrong_answer():
    trace = {'status':'success','final_response':'{"files":["wrong.txt"],"files":["a.txt"]}',
             'tool_calls':[{'name':'list_directory','params':{'path':'fixture'}}]}
    assert evaluate_listing(trace,Path('fixture'),['a.txt'],True)['passed'] is False
