"""Unit tests for ConfigManager: missing-key fast-fail, env var override
precedence, and secrets.yaml loading. Refs #216."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.shared.config_manager import ConfigManager


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
    # ConfigManager is a classmethod-only singleton (module-level class attrs),
    # so leaving it "loaded" with this test's tmp_path config would leak into
    # every later test in the same pytest session that expects the real
    # config/config.yaml (monkeypatch.chdir only restores cwd, not this state).
    ConfigManager._config = {}
    ConfigManager._prefs = {}
    ConfigManager._profiles = {}
    ConfigManager._loaded = False


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_get_active_model_raises_when_unconfigured(config_dir: Path):
    """No active_model anywhere -> fast-fail RuntimeError, not a silent None."""
    _write_yaml(config_dir / "config.yaml", {})

    ConfigManager.load_config()

    with pytest.raises(RuntimeError, match="No active AI model configured"):
        ConfigManager.get_active_model()


def test_get_active_model_prefers_top_level_over_legacy_text_model(config_dir: Path):
    _write_yaml(
        config_dir / "config.yaml",
        {"active_model": "google/gemma-4-26b-a4b-qat", "models": {"text": {"model": "legacy/model"}}},
    )

    ConfigManager.load_config()

    assert ConfigManager.get_active_model() == "google/gemma-4-26b-a4b-qat"


def test_get_active_model_falls_back_to_legacy_text_model(config_dir: Path):
    _write_yaml(config_dir / "config.yaml", {"models": {"text": {"model": "legacy/model"}}})

    ConfigManager.load_config()

    assert ConfigManager.get_active_model() == "legacy/model"


def test_get_returns_default_for_missing_key_path(config_dir: Path):
    _write_yaml(config_dir / "config.yaml", {"tools": {}})

    ConfigManager.load_config()

    assert ConfigManager.get("tools.nonexistent", "fallback") == "fallback"
    assert ConfigManager.get("entirely.missing.path") is None


def test_secrets_yaml_is_merged_into_config(config_dir: Path, monkeypatch: pytest.MonkeyPatch):
    # HUBSPACE_USERNAME is special-cased: load_config() always overlays it from
    # the OS environment (step 5), so clear it to isolate the secrets.yaml merge.
    monkeypatch.delenv("HUBSPACE_USERNAME", raising=False)
    _write_yaml(config_dir / "config.yaml", {"active_model": "base-model"})
    _write_yaml(config_dir / "secrets.yaml", {"hubspace": {"username": "from-secrets"}})

    ConfigManager.load_config()

    assert ConfigManager.get("hubspace.username") == "from-secrets"


def test_preferences_override_secrets_which_override_base(config_dir: Path):
    _write_yaml(config_dir / "config.yaml", {"shared_key": "base"})
    _write_yaml(config_dir / "secrets.yaml", {"shared_key": "secret"})
    _write_yaml(config_dir / "preferences.yaml", {"shared_key": "preference"})

    ConfigManager.load_config()

    assert ConfigManager.get("shared_key") == "preference"


def test_get_secret_falls_back_to_environment_variable(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_yaml(config_dir / "config.yaml", {})
    monkeypatch.setenv("HUBSPACE_USERNAME", "from-env")

    ConfigManager.load_config()

    assert ConfigManager.get_secret("hubspace.username") == "from-env"


def test_get_secret_prefers_config_over_environment_variable(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    # Use a key with no special env-injection handling (unlike hubspace.*,
    # see load_config() step 5) so this isolates get_secret()'s own precedence.
    _write_yaml(config_dir / "config.yaml", {"custom": {"secret_key": "from-config-real-value"}})
    monkeypatch.setenv("CUSTOM_SECRET_KEY", "from-env")

    ConfigManager.load_config()

    assert ConfigManager.get_secret("custom.secret_key") == "from-config-real-value"


def test_get_secret_skips_placeholder_and_uses_env(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """secrets.yaml stubs must not shadow a real .env key (Brave / evals)."""
    _write_yaml(
        config_dir / "secrets.yaml",
        {"BRAVE_API_KEY": "your-brave-search-api-key-here"},
    )
    _write_yaml(config_dir / "config.yaml", {})
    monkeypatch.setenv("BRAVE_API_KEY", "BSAM_real_key_from_env_xxxxxxxx")

    ConfigManager.load_config()

    assert ConfigManager.get_secret("BRAVE_API_KEY") == "BSAM_real_key_from_env_xxxxxxxx"


def test_get_secret_returns_default_when_absent_everywhere(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_yaml(config_dir / "config.yaml", {})
    monkeypatch.delenv("SOME_MISSING_SECRET", raising=False)

    ConfigManager.load_config()

    assert ConfigManager.get_secret("some.missing.secret", "default") == "default"


def test_hubspace_env_vars_injected_into_config_on_load(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_yaml(config_dir / "config.yaml", {})
    monkeypatch.setenv("HUBSPACE_USERNAME", "env-user")
    monkeypatch.setenv("HUBSPACE_PASSWORD", "env-pass")

    ConfigManager.load_config()

    assert ConfigManager.get("hubspace.username") == "env-user"
    assert ConfigManager.get("hubspace.password") == "env-pass"


def test_missing_config_file_does_not_crash_load(config_dir: Path):
    """No config.yaml on disk at all -> empty base config, not an exception."""
    ConfigManager.load_config()

    assert ConfigManager.get_config() == {} or isinstance(ConfigManager.get_config(), dict)


def test_malformed_secrets_yaml_does_not_crash_load(config_dir: Path):
    (config_dir / "secrets.yaml").write_text(": not: valid: yaml: [", encoding="utf-8")
    _write_yaml(config_dir / "config.yaml", {"active_model": "base-model"})

    ConfigManager.load_config()

    assert ConfigManager.get("active_model") == "base-model"
