"""First-run checks must not depend on a maintainer's model, PATH or accounts."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.shared.config_manager import ConfigManager
from src.infrastructure.services import setup_wizard as wizard


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(ConfigManager, "_config", {"local_models": {"use_local": True}})
    monkeypatch.setattr(ConfigManager, "_loaded", True)
    monkeypatch.setattr(wizard, "bootstrap_config_if_needed", lambda: [])
    monkeypatch.setattr(wizard._lms, "find_lms_cli", lambda: None)
    monkeypatch.setattr(wizard._lms, "find_lmstudio_app", lambda: None)
    monkeypatch.setattr(wizard._lms, "is_server_running", lambda: False)


def test_fresh_install_has_no_personal_model_selection(fresh):
    baseline = yaml.safe_load((Path(__file__).parents[2] / "config/config.yaml").read_text(encoding="utf-8"))
    template = yaml.safe_load((Path(__file__).parents[2] / "config/preferences.yaml.example").read_text(encoding="utf-8"))
    for config in (baseline, baseline["local_models"], template["local_models"]):
        assert config.get("active_model") is None
        assert config.get("supervisor") is None
    assert ConfigManager.get_active_model() == ""
    assert ConfigManager.get_local_model_config() == {}
    assert wizard.get_setup_status()["needs_setup"] is True


def test_server_is_checked_without_cli_and_does_not_select_a_model(fresh, monkeypatch):
    monkeypatch.setattr(wizard._lms, "is_server_running", lambda: True)
    status = wizard.get_setup_status()
    assert status["server_running"] is True
    assert status["lms_cli_found"] is False
    assert status["lmstudio_installed"] is True
    assert status["needs_setup"] is True


def test_installed_app_does_not_require_cli(fresh, monkeypatch):
    monkeypatch.setattr(wizard._lms, "find_lmstudio_app", lambda: "fictional-app.exe")
    status = wizard.get_setup_status()
    assert status["lmstudio_installed"] is True
    assert status["server_running"] is False


def test_cli_is_found_after_install_without_path_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard._lms.shutil, "which", lambda name: None)
    monkeypatch.setattr(wizard._lms.Path, "home", classmethod(lambda cls: tmp_path))
    directory = tmp_path / ".lmstudio" / "bin"
    directory.mkdir(parents=True)
    filename = "lms.exe" if wizard._lms.sys.platform == "win32" else "lms"
    expected = directory / filename
    expected.write_bytes(b"")
    assert wizard._lms.find_lms_cli() == str(expected)


@pytest.mark.parametrize("provider", ["lmstudio", "ollama", "custom"])
def test_single_model_choice_survives_reload_without_detection(fresh, monkeypatch, tmp_path, provider):
    from src.infrastructure.repositories.config_repository import ConfigRepository
    repo = ConfigRepository(str(tmp_path))
    repo.save("preferences", {"unrelated": "preserve"})
    monkeypatch.setattr("src.infrastructure.repositories.ConfigRepository", lambda: repo)
    result = wizard.save_provider_choice(provider, "http://localhost:1234/v1", "", "fictional-chat", False)
    assert result["success"]
    persisted = repo.load("preferences", use_cache=False)
    assert persisted["unrelated"] == "preserve"
    monkeypatch.setattr(ConfigManager, "_config", persisted)
    monkeypatch.setattr(wizard._lms, "find_lms_cli", lambda: pytest.fail("Saved choices must not require CLI"))
    assert wizard.get_setup_status()["needs_setup"] is False
    selected = ConfigManager.get_local_model_config()
    assert selected["name"] == "fictional-chat"
    assert selected["provider"] == ("static" if provider == "custom" else provider)
    assert ConfigManager.get_active_model() == "fictional-chat"


def test_failed_save_does_not_report_setup_complete(fresh, monkeypatch):
    repo = MagicMock()
    repo.load.return_value = {}
    repo.save.return_value = False
    monkeypatch.setattr("src.infrastructure.repositories.ConfigRepository", lambda: repo)
    result = wizard.save_provider_choice("lmstudio", "http://localhost:1234/v1", "", "fictional-chat")
    assert not result["success"]
    assert ConfigManager.get_local_model_config() == {}


@pytest.mark.parametrize("success", [True, False])
def test_desktop_refreshes_its_separate_cache_after_setup(fresh, monkeypatch, success):
    from src.clients.desktop.api.config import ConfigAPIMixin

    bridge = ConfigAPIMixin()
    bridge._execute_command = MagicMock(return_value={"success": success})
    selected = {"local_models": {"use_local": True,
                "supervisor": {"name": "fictional-chat", "provider": "lmstudio"}}}
    reload_config = MagicMock(side_effect=lambda: setattr(ConfigManager, "_config", selected))
    monkeypatch.setattr(ConfigManager, "load_config", reload_config)

    assert bridge.save_provider_choice("lmstudio", "http://localhost:1234/v1", "", "fictional-chat")["success"] == success
    assert ConfigManager.get_active_model() == ("fictional-chat" if success else "")
    assert reload_config.call_count == int(success)


def test_legacy_template_model_is_not_a_completed_setup(fresh, monkeypatch):
    ConfigManager._config["local_models"]["active_model"] = {
        "name": "old-template-model", "provider": "lmstudio", "base_url": "http://localhost:1234/v1"
    }
    assert wizard.get_setup_status()["needs_setup"] is True
    # A current model-picker choice is preserved even when its server is down.
    ConfigManager._config["local_models"]["supervisor"] = dict(ConfigManager._config["local_models"]["active_model"])
    assert wizard.get_setup_status()["needs_setup"] is False


@pytest.mark.parametrize("providers, expected", [([], []), (["gemini"], ["gemini-2.5-flash"]), (["anthropic"], ["anthropic/claude-example"])])
def test_cloud_picker_requires_provider_credentials(monkeypatch, providers, expected):
    from src.clients.desktop.api.cloud_models import available_cloud_models
    from src.core.intelligence import provider_failover
    monkeypatch.setattr(provider_failover, "scan_available_providers", lambda: providers)
    monkeypatch.setattr(ConfigManager, "get", lambda *args: [
        {"id": "gemini-2.5-flash"}, {"id": "anthropic/claude-example"}, {"id": "unknown"}
    ])
    assert [m["id"] for m in available_cloud_models()] == expected
