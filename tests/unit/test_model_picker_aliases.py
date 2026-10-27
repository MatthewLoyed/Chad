import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.shared.config_manager import ConfigManager
from src.infrastructure.repositories.config_repository import ConfigRepository


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    repo = ConfigRepository(str(tmp_path))
    repo.save("preferences", {"local_models": {"use_local": True,
        "supervisor": {"name": "lfm-old"}, "active_model": {"name": "lfm-old"}},
        "unrelated": "preserve"})
    monkeypatch.setattr(ConfigManager, "_config", {"local_models": {
        "use_local": True, "supervisor": {"name": "lfm-old"}}})
    monkeypatch.setattr(ConfigManager, "_loaded", True)
    monkeypatch.setattr("src.infrastructure.repositories.ConfigRepository", lambda: repo)
    monkeypatch.setattr("src.infrastructure.repositories.config_repository.ConfigRepository", lambda: repo)
    return repo


@pytest.mark.asyncio
async def test_gui_selection_survives_reload_and_reaches_brain(monkeypatch, isolated_config):
    from src.application.facades.assistant.models import ModelsFacadeMixin
    adapter = MagicMock()
    adapter.is_ready = AsyncMock(return_value=True)
    switch = AsyncMock(return_value=True)
    monkeypatch.setattr("src.core.intelligence.model_lifecycle.switch_main_model", switch)
    monkeypatch.setattr("src.core.intelligence.adapters.litellm_adapter.LiteLLMAdapter", lambda **kw: adapter)
    monkeypatch.setattr(ConfigManager, "get_persona_instruction", classmethod(lambda cls: "test"))
    facade = ModelsFacadeMixin()
    facade._text_mode_client = MagicMock()
    facade._assistant = MagicMock()
    facade._is_uplink = True
    facade._uplink = SimpleNamespace(send_model_update=AsyncMock())
    selected = {"name": "prism-ml/bonsai-27b", "provider": "lmstudio", "base_url": "http://localhost:1234/v1"}
    result = await facade.set_local_model(selected)
    assert result["success"]
    persisted = isolated_config.load("preferences", use_cache=False)
    assert persisted["unrelated"] == "preserve"
    assert persisted["local_models"]["supervisor"] == persisted["local_models"]["active_model"]
    monkeypatch.setattr(ConfigManager, "_config", persisted)
    assert ConfigManager.get_active_model() == selected["name"]
    assert ConfigManager.get_local_model_config("agent")["name"] == selected["name"]
    update = facade._uplink.send_model_update.await_args.args[0]
    assert update["local_models"]["supervisor"]["name"] == selected["name"]
    switch.assert_awaited_once()
    assert switch.await_args.args[1] == selected
    assert result.get("warning") is None


def test_old_client_update_clears_stale_supervisor_pin(isolated_config):
    from src.remote.limb_message_handler import LimbMessageHandler
    events = MagicMock()
    handler = LimbMessageHandler(MagicMock(), events, asyncio.Event())
    payload = {"local_models": {"active_model": {"name": "bonsai-new"}, "use_local": True}}
    handler._handle_set_model_config(SimpleNamespace(payload=payload))
    assert "supervisor" not in payload["local_models"]
    assert ConfigManager.get_active_model() == "bonsai-new"
    persisted = isolated_config.load("preferences", use_cache=False)
    assert persisted["local_models"]["supervisor"]["name"] == "bonsai-new"
    assert events.publish.call_args.args[0].new_model == "bonsai-new"


@pytest.mark.asyncio
async def test_failed_load_does_not_change_preferences_or_adapter(monkeypatch, isolated_config):
    from src.application.facades.assistant.models import ModelsFacadeMixin
    monkeypatch.setattr("src.core.intelligence.model_lifecycle.switch_main_model", AsyncMock(return_value=False))
    facade = ModelsFacadeMixin()
    facade._text_mode_client = SimpleNamespace(adapter="previous-adapter")
    result = await facade.set_local_model({"name": "new", "provider": "lmstudio",
                                           "base_url": "http://fixture.invalid/v1"})
    assert not result["success"]
    assert result["failure_class"] == "unavailable"
    assert facade._text_mode_client.adapter == "previous-adapter"
    assert isolated_config.load("preferences", use_cache=False)["local_models"]["supervisor"]["name"] == "lfm-old"


def test_setup_choice_routes_immediately_and_survives_reload(monkeypatch, isolated_config):
    from src.application.facades.assistant.config import ConfigFacadeMixin
    from src.infrastructure.services import setup_wizard
    facade = ConfigFacadeMixin()
    facade._assistant = SimpleNamespace(model_router=MagicMock())
    result = facade.save_provider_choice('custom', 'http://localhost:8000/v1', '', 'qwen3-smoke')
    assert result['success']
    assert ConfigManager.get_active_model() == 'qwen3-smoke'
    assert ConfigManager.get_local_model_config('chat')['base_url'] == 'http://localhost:8000/v1'
    facade._assistant.model_router.reload_models.assert_called_once()
    monkeypatch.setattr(ConfigManager, '_config', isolated_config.load('preferences', use_cache=False))
    monkeypatch.setattr(setup_wizard, 'bootstrap_config_if_needed', lambda: [])
    monkeypatch.setattr(setup_wizard._lms, 'find_lms_cli', lambda: pytest.fail('Custom setup must not require LM Studio'))
    assert ConfigManager.get_active_model() == 'qwen3-smoke'
    assert setup_wizard.get_setup_status()['needs_setup'] is False
    assert ConfigManager.get('setup.share_main_model') is True
    assert ConfigManager.get('tools.llm_selector_model') == 'qwen3-smoke'
    assert ConfigManager.get('intent_router.enabled') is False


def test_slow_child_boot_does_not_spawn_another_brain():
    from src.remote.limb import ChadLimb
    limb = ChadLimb.__new__(ChadLimb)
    limb.server_url = 'ws://localhost:8788/ws'
    limb._autostarted_brain = MagicMock()
    limb._autostarted_brain.poll.return_value = None
    limb._is_server_healthy = MagicMock()
    limb._try_autostart_brain()
    limb._is_server_healthy.assert_not_called()
