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


@pytest.mark.parametrize("frozen", [False, True])
def test_hands_launch_uses_explicit_frozen_dispatch(monkeypatch, frozen):
    import sys
    from src.clients.desktop.helper_process import hands_command
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    command = hands_command()
    assert command[0] == sys.executable
    if frozen:
        assert command[1:] == ["--hands-daemon"]
    else:
        assert command[1].endswith("hands.py")


def test_frozen_hands_dispatch_never_imports_gui(monkeypatch):
    import sys
    from src.shared import frozen_bootstrap
    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["Chad.exe", "--hands-daemon"])
    monkeypatch.setattr(__import__('multiprocessing'), "freeze_support", lambda: None)
    monkeypatch.setattr(frozen_bootstrap, "bootstrap_frozen", lambda: None)
    monkeypatch.setattr(frozen_bootstrap, "redirect_stdio_for_frozen_windowed", lambda: None)
    monkeypatch.setattr(runpy, "run_module", lambda name, **kw: calls.append((name, kw)))
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(ROOT / 'main_gui.py'), run_name='__main__')
    assert stopped.value.code == 0
    assert calls == [("src.daemons.hands", {"run_name": "__main__"})]


@pytest.mark.parametrize("state", ["ready", "initializing", "reconnecting"])
def test_desktop_status_poll_recovers_missed_startup_push(state):
    from src.clients.desktop.api.status import StatusAPIMixin
    window = SimpleNamespace(current_status_text="Connected", _current_state_enum=state)
    bridge = StatusAPIMixin()
    bridge.main_window_ref = lambda: window
    assert bridge.get_raw_state() == state
    assert bridge.get_status() == "Connected"


def test_hands_reads_newly_generated_profile_token(monkeypatch):
    from src.daemons.hands import _profile_auth_token
    from src.shared.config_manager import ConfigManager
    calls = []
    monkeypatch.setattr(ConfigManager, "load_config", lambda: calls.append("reload"))
    monkeypatch.setattr(ConfigManager, "get_secret", lambda key: "fictional-profile-token" if key == "CHAD_SERVER_TOKEN" else None)
    assert _profile_auth_token() == "fictional-profile-token"
    assert calls == ["reload"]
