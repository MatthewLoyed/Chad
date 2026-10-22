"""Unit tests for preferences.profile overlay on profiles.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.shared.config_manager import ConfigManager
from src.infrastructure.repositories.config_repository import ConfigRepository


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.chdir(tmp_path)
    ConfigManager._config = {}
    ConfigManager._prefs = {}
    ConfigManager._profiles = {}
    ConfigManager._loaded = False
    yield cfg
    # See test_config_manager.py's config_dir fixture: ConfigManager is a
    # classmethod-only singleton, so this must reset back to "unloaded" or it
    # leaks this test's tmp_path config into later tests in the same session.
    ConfigManager._config = {}
    ConfigManager._prefs = {}
    ConfigManager._profiles = {}
    ConfigManager._loaded = False


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_preferences_profile_overlays_active_profile(config_dir: Path):
    _write_yaml(
        config_dir / "config.yaml",
        {"active_model": "supervisor-model", "tools": {"llm_selector_model": "qwen/qwen3.5-9b"}, "voice_name": "Default"},
    )
    _write_yaml(
        config_dir / "profiles.yaml",
        {
            "active_profile": "default",
            "profiles": {
                "default": {
                    "name": "User",
                    "apps": {"chrome": {"path": "C:\\Chrome\\chrome.exe"}},
                    "routines": {},
                }
            },
        },
    )
    _write_yaml(
        config_dir / "preferences.yaml",
        {
            "profile": {
                "monitors": {"layout": ["center", "left"]},
                "apps": {"minecraft": {"path": "C:\\Games\\Minecraft.exe"}},
                "routines": {
                    "gaming": [{"action": "launch_app", "app": "minecraft", "monitor": 1}]
                },
                "routine_builder_model": "qwen/qwen3.5-9b",
            }
        },
    )

    ConfigManager.load_config()
    profile = ConfigManager.get_profile()

    assert profile["monitors"]["layout"] == ["center", "left"]
    assert "minecraft" in profile["apps"]
    assert profile["apps"]["chrome"]["path"] == "C:\\Chrome\\chrome.exe"
    assert profile["routines"]["gaming"][0]["app"] == "minecraft"
    assert ConfigManager.get("apps.minecraft.path") == "C:\\Games\\Minecraft.exe"
    assert ConfigManager.get_routine_builder_model() == "supervisor-model"


def test_profile_key_not_merged_into_top_level_config(config_dir: Path):
    _write_yaml(config_dir / "config.yaml", {})
    _write_yaml(
        config_dir / "profiles.yaml",
        {"active_profile": "default", "profiles": {"default": {}}},
    )
    _write_yaml(
        config_dir / "preferences.yaml",
        {"profile": {"name": "ShouldNotLeak"}, "ui": {"default_turn_mode": "agent"}},
    )

    ConfigManager.load_config()

    assert "ShouldNotLeak" not in ConfigManager.get_config()
    assert ConfigManager.get("ui.default_turn_mode") == "agent"


def test_save_profile_writes_active_overlay_to_preferences(config_dir: Path):
    _write_yaml(config_dir / "config.yaml", {})
    _write_yaml(
        config_dir / "profiles.yaml",
        {"active_profile": "default", "profiles": {"default": {"name": "User"}}},
    )
    _write_yaml(config_dir / "preferences.yaml", {})

    repo = ConfigRepository(str(config_dir))
    assert repo.save_profile("default", {"monitors": {"layout": ["laptop"]}})

    prefs = yaml.safe_load((config_dir / "preferences.yaml").read_text(encoding="utf-8"))
    assert prefs["profile"]["monitors"]["layout"] == ["laptop"]

    profiles = yaml.safe_load((config_dir / "profiles.yaml").read_text(encoding="utf-8"))
    assert "monitors" not in profiles["profiles"]["default"]


def test_legacy_routine_model_setter_cannot_override_supervisor(config_dir: Path):
    _write_yaml(config_dir / "config.yaml", {"active_model": "supervisor-model"})
    ConfigManager.load_config()
    assert not ConfigManager.set_routine_builder_model("small-model")
    assert ConfigManager.set_routine_builder_model("supervisor-model")
    assert ConfigManager.get_routine_builder_model() == "supervisor-model"
    assert "routine_builder_model" not in ConfigManager.get_profile()
