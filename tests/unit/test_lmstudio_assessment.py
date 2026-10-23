import json
from types import SimpleNamespace

import pytest

from src.infrastructure.services import lmstudio_assessment as lm


@pytest.mark.parametrize('url', ['https://example.com/v1', 'http://localhost@evil.test/v1',
                                'file:///models', 'http://localhost:1234/v1?secret=x'])
def test_remote_or_ambiguous_endpoints_rejected(url):
    with pytest.raises(ValueError):
        lm.local_url(url)


def test_estimate_never_loads_or_changes_defaults(monkeypatch):
    calls = []
    monkeypatch.setattr(lm, 'find_cli', lambda: 'lms')
    def cli(args):
        calls.append(args)
        output = '--estimate-only --context-length --gpu' if '--help' in args else (
            json.dumps([{'type': 'llm', 'modelKey': 'example-chat'}]) if 'ls' in args else 'Estimated GPU Memory: 5 GB')
        return SimpleNamespace(returncode=0, stdout=output, stderr='')
    monkeypatch.setattr(lm, '_cli', cli)
    assert lm.estimate('example-chat', 8192, 'max')['success']
    assert '--estimate-only' in calls[-1]
    assert not any('unload' in args or 'get' in args for args in calls)


def test_unadvertised_flags_and_unconfirmed_benchmark_do_nothing(monkeypatch):
    monkeypatch.setattr(lm, 'find_cli', lambda: 'lms')
    monkeypatch.setattr(lm, '_cli', lambda args: SimpleNamespace(stdout='older CLI', returncode=0))
    assert lm.estimate('example', 8192, 'max')['failure_class'] == 'capability_gap'
    assert lm.benchmark('http://localhost:1234/v1', '', 'example')['failure_class'] == 'bad_input'
