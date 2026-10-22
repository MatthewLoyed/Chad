import os
from unittest.mock import patch
from evals.runtime_bootstrap import bootstrap_path

def test_existing_commands_preserve_path(monkeypatch):
    monkeypatch.setenv('PATH','existing-priority')
    with patch('evals.runtime_bootstrap.shutil.which',return_value='existing'):
        result=bootstrap_path()
    assert result['added']==[]
    assert os.environ['PATH']=='existing-priority'

def test_missing_dependencies_are_reported_without_system_mutation(monkeypatch):
    monkeypatch.setenv('PATH','original')
    with patch('evals.runtime_bootstrap.shutil.which',return_value=None),patch('evals.runtime_bootstrap.Path.is_dir',return_value=False):
        result=bootstrap_path()
    assert not any(result['available'].values())
    assert os.environ['PATH']=='original'
