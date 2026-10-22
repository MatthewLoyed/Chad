"""Unit tests for Presenton sidecar settings sync."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.services import presenton_sidecar as ps
from src.shared.config_manager import ConfigManager


def test_write_presenton_env(tmp_path: Path):
    env_path = ps.write_presenton_env(
        tmp_path,
        llm_url="http://host.docker.internal:1234/v1",
        model="qwen/qwen3.5-9b",
        auth_username="chad",
        auth_password="test-secret-123",
    )
    assert env_path.is_file()
    text = env_path.read_text(encoding="utf-8")
    assert "CUSTOM_MODEL=qwen/qwen3.5-9b" in text
    assert "CUSTOM_LLM_URL=http://host.docker.internal:1234/v1" in text
    assert "AUTH_USERNAME=chad" in text
    assert "AUTH_PASSWORD=test-secret-123" in text
    assert "AUTH_OVERRIDE_FROM_ENV" not in text


@pytest.mark.asyncio
async def test_set_presenton_settings_persists_and_restarts(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docker" / "presenton").mkdir(parents=True)
    (tmp_path / "docker" / "chad-services.yml").write_text("services: {}\n", encoding="utf-8")

    saved: dict = {}

    class FakeRepo:
        def load(self, name, use_cache=False):
            return saved.get(name, {})

        def save(self, name, data, update_cache=True):
            saved[name] = data
            return True

    # A live dict (not a static lambda) so ConfigManager.get() reflects the
    # visual_update that set_presenton_settings() applies mid-call via
    # _recursive_update(get_config(), {"visual": visual_update}) — a static
    # mock here would make the post-save `merged = get_presenton_settings()`
    # read stale (pre-update) values and silently skip the restart branch (#216).
    live_config: dict = {"visual": {}}

    def _fake_get(key, default=None):
        auth_values = {
            "visual.presenton_auth_username": "chad",
            "visual.presenton_auth_password": "test-secret-123",
        }
        if key in auth_values:
            return auth_values[key]
        node = live_config
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node if node is not None else default

    with patch.object(ps, "get_project_root", return_value=tmp_path), patch(
        "src.infrastructure.repositories.ConfigRepository", FakeRepo
    ), patch.object(
        ps.ConfigManager, "get", side_effect=_fake_get
    ), patch.object(
        ps.ConfigManager, "get_config", return_value=live_config
    ), patch.object(
        ps.ConfigManager, "_recursive_update", side_effect=ConfigManager._recursive_update
    ), patch.object(
        ps, "restart_presenton_sidecar",
        new_callable=AsyncMock,
        return_value=(True, "restarted"),
    ), patch.object(
        ps, "_presenton_connected",
        new_callable=AsyncMock,
        return_value=False,
    ), patch.object(
        ps, "_wait_until_connected",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await ps.set_presenton_settings(
            enabled=True,
            model="google/gemma-4-26b-a4b-qat",
            restart_sidecar=True,
        )

    assert result["success"] is True
    assert saved["preferences"]["visual"]["presenton_url"] == ps.DEFAULT_PRESENTON_URL
    assert saved["preferences"]["visual"]["presenton_model"] == "google/gemma-4-26b-a4b-qat"
    env_text = (tmp_path / ps.ENV_PATH_REL).read_text(encoding="utf-8")
    assert "google/gemma-4-26b-a4b-qat" in env_text
    assert "AUTH_USERNAME=chad" in env_text


@pytest.mark.asyncio
async def test_connected_presenton_is_never_reconfigured(tmp_path: Path):
    restart = AsyncMock()
    with patch.object(ps, "get_project_root", return_value=tmp_path), patch.object(
        ps, "get_presenton_settings",
        return_value={
            "enabled": True,
            "url": ps.DEFAULT_PRESENTON_URL,
            "llm_url": ps.DEFAULT_LLM_URL,
            "model": "existing-model",
            "use_async": False,
        },
    ), patch.object(
        ps, "_presenton_connected",
        new_callable=AsyncMock,
        return_value=True,
    ), patch.object(ps, "restart_presenton_sidecar", restart), patch.object(
        ps, "_apply_local_sidecar_config",
        new_callable=AsyncMock,
    ) as apply_config:
        result = await ps.set_presenton_settings(restart_sidecar=True)

    assert result["success"] is True
    assert result["connected"] is True
    assert result["sidecar_restarted"] is False
    assert not (tmp_path / ps.ENV_PATH_REL).exists()
    restart.assert_not_awaited()
    apply_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_change_syncs_env_when_already_connected(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docker" / "presenton").mkdir(parents=True)
    env_path = tmp_path / ps.ENV_PATH_REL
    env_path.write_text(
        "AUTH_USERNAME=chad\nAUTH_PASSWORD=secret\nCUSTOM_MODEL=old-model\n",
        encoding="utf-8",
    )
    (tmp_path / "docker" / "chad-services.yml").write_text("services: {}\n", encoding="utf-8")

    saved: dict = {"preferences": {"visual": {"presenton_url": ps.DEFAULT_PRESENTON_URL}}}

    class FakeRepo:
        def load(self, name, use_cache=False):
            return saved.get(name, {})

        def save(self, name, data, update_cache=True):
            saved[name] = data
            return True

    live_config: dict = {"visual": {"presenton_url": ps.DEFAULT_PRESENTON_URL}}

    def _fake_get(key, default=None):
        auth_values = {
            "visual.presenton_auth_username": "chad",
            "visual.presenton_auth_password": "secret",
            "visual.presenton_llm_url": ps.DEFAULT_LLM_URL,
        }
        if key in auth_values:
            return auth_values[key]
        node = live_config
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node if node is not None else default

    apply = AsyncMock(return_value=(True, "Presenton now uses new-model.", True))
    with patch.object(ps, "get_project_root", return_value=tmp_path), patch(
        "src.infrastructure.repositories.ConfigRepository", FakeRepo
    ), patch.object(
        ps.ConfigManager, "get", side_effect=_fake_get
    ), patch.object(
        ps.ConfigManager, "get_config", return_value=live_config
    ), patch.object(
        ps.ConfigManager, "_recursive_update", side_effect=ConfigManager._recursive_update
    ), patch.object(
        ps, "_apply_local_sidecar_config", apply
    ):
        result = await ps.set_presenton_settings(
            model="google/gemma-4-26b-a4b-qat",
            restart_sidecar=False,
        )

    assert result["success"] is True
    assert saved["preferences"]["visual"]["presenton_model"] == "google/gemma-4-26b-a4b-qat"
    apply.assert_awaited_once()
    merged_arg = apply.await_args.args[0]
    assert merged_arg["model"] == "google/gemma-4-26b-a4b-qat"


@pytest.mark.asyncio
async def test_external_presenton_failure_never_touches_local_docker(tmp_path: Path):
    restart = AsyncMock()
    with patch.object(ps, "get_project_root", return_value=tmp_path), patch.object(
        ps, "get_presenton_settings",
        return_value={
            "enabled": True,
            "url": "https://presenton.example.test",
            "llm_url": ps.DEFAULT_LLM_URL,
            "model": "existing-model",
            "use_async": False,
        },
    ), patch.object(
        ps, "_presenton_connected",
        new_callable=AsyncMock,
        return_value=False,
    ), patch.object(ps, "restart_presenton_sidecar", restart):
        result = await ps.set_presenton_settings(restart_sidecar=True)

    assert result["success"] is False
    assert "external Presenton" in result["error"]
    assert not (tmp_path / ps.ENV_PATH_REL).exists()
    restart.assert_not_awaited()
