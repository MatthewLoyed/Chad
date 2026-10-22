"""Frozen multiprocessing workers must not initialize a GUI or bind a Brain port."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('entrypoint', ['main_gui.py', 'src/remote/server.py'])
def test_worker_diverts_before_application_imports(monkeypatch, entrypoint):
    calls = []

    def freeze_support():
        calls.append('worker')
        raise SystemExit(0)

    monkeypatch.setitem(__import__('sys').modules, 'multiprocessing', SimpleNamespace(freeze_support=freeze_support))
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(ROOT / entrypoint), run_name='__main__')
    assert stopped.value.code == 0
    assert calls == ['worker']


@pytest.mark.parametrize('url', ['http://localhost:5173/?theme=dark#chat'])
def test_desktop_url_preserves_existing_query_and_fragment(url):
    from urllib.parse import parse_qs, urlsplit
    from src.clients.desktop.main_window import _desktop_ui_url

    marked = urlsplit(_desktop_ui_url(url))
    assert parse_qs(marked.query) == {'theme': ['dark'], 'chadDesktop': ['1']}
    assert marked.fragment == 'chat'


def test_local_file_url_is_not_modified_for_webview2():
    from src.clients.desktop.main_window import _desktop_ui_url

    url = 'file:///C:/Chad%20Alpha/index.html'
    assert _desktop_ui_url(url) == url
