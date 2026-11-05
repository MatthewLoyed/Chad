import importlib.util
from pathlib import Path

import httpx


def load_script():
    spec = importlib.util.spec_from_file_location('accept_ollama', Path('scripts/setup/accept_ollama.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_model_requires_download_authorization(monkeypatch):
    script = load_script()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'models': []})))
    monkeypatch.setattr(script.httpx, 'Client', lambda **kw: client)
    monkeypatch.setattr(script, 'pull', lambda *a: (_ for _ in ()).throw(AssertionError('unauthorized download')))
    result = script.accept('http://localhost:11434/v1', 'qwen3.5:4b')
    assert result['status'] == 'not_run' and result['generation_requests'] == 0


def test_authorized_acquisition_checks_second_call_reuses_model(monkeypatch):
    script = load_script()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'models': []})))
    monkeypatch.setattr(script.httpx, 'Client', lambda **kw: client)
    calls = []
    def pull(options, cancel, update):
        calls.append(options)
        if len(calls) == 1:
            update(bytes=100, total=100)
        return {'success': True}
    monkeypatch.setattr(script, 'pull', pull)
    result = script.accept('http://localhost:11434/v1', 'qwen3.5:4b', download=True)
    assert result['status'] == 'passed' and result['reuse_without_transfer']
    assert result['progress_events'] == 1 and len(calls) == 2
