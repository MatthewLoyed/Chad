"""Branch testing reuses installed setup; clean profiles still onboard."""
import os

import pytest

from src.infrastructure.services import setup_wizard as wizard
from src.shared.config_manager import ConfigManager


@pytest.mark.parametrize("shared, dev, selected, expected", [
    (True, True, True, False),
    (False, True, True, True),
    (True, False, True, True),
    (True, True, False, True),
])
def test_existing_profile_requires_shared_preferences_and_model(
    monkeypatch, tmp_path, shared, dev, selected, expected
):
    main = tmp_path / "main"
    branch = tmp_path / "branch"
    for root in (main, branch):
        (root / "config").mkdir(parents=True)
    prefs = main / "config/preferences.yaml"
    prefs.write_text("{}", encoding="utf-8")
    local = branch / "config/preferences.yaml"
    if shared:
        os.link(prefs, local)
    else:
        local.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CHAD_MAIN_ROOT", str(main))
    monkeypatch.setenv("CHAD_DEV_WORKTREE", "1" if dev else "0")
    monkeypatch.setattr(wizard, "get_project_root", lambda: branch)
    monkeypatch.setattr(wizard, "bootstrap_config_if_needed", lambda: [])
    monkeypatch.setattr(ConfigManager, "_loaded", True)
    monkeypatch.setattr(ConfigManager, "_config", {"local_models": {
        "use_local": True,
        "active_model": {"name": "fictional-chat", "provider": "lmstudio",
                         "base_url": "http://localhost:1234/v1"} if selected else None,
    }})
    monkeypatch.setattr(wizard._lms, "find_lms_cli", lambda: None)
    monkeypatch.setattr(wizard._lms, "find_lmstudio_app", lambda: None)
    monkeypatch.setattr(wizard._lms, "is_server_running", lambda: False)
    assert wizard.get_setup_status()["needs_setup"] is expected


def test_missing_main_profile_does_not_skip_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAD_MAIN_ROOT", str(tmp_path / "missing"))
    monkeypatch.setenv("CHAD_DEV_WORKTREE", "1")
    monkeypatch.setattr(wizard, "get_project_root", lambda: tmp_path)
    assert not wizard._uses_existing_dev_profile()
