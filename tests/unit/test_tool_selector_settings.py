from types import SimpleNamespace

import pytest

from src.application.commands.tool_selector_commands import (
    GetToolSelectorModelCommand, SetToolSelectorModelCommand,
)
from src.shared.config_manager import ConfigManager
from src.infrastructure.repositories.config_repository import ConfigRepository
from src.infrastructure.tools.selection.configuration import configured_selector
from src.infrastructure.tools.selection.llm_selector import LLMToolSelector


@pytest.mark.asyncio
async def test_selector_choice_persists_without_changing_main_model(monkeypatch, tmp_path):
    repo = ConfigRepository(str(tmp_path))
    repo.save("preferences", {"unrelated": "preserved"})
    monkeypatch.setattr("src.application.commands.tool_selector_commands.ConfigRepository", lambda: repo)
    monkeypatch.setattr(ConfigManager, "_loaded", True)
    monkeypatch.setattr(ConfigManager, "_config", {"local_models": {"supervisor": {"name": "chat-main"}}})
    registry = SimpleNamespace(_llm_selector=LLMToolSelector("old", "http://localhost:1234/v1"))
    assert await SetToolSelectorModelCommand("selector-small", "ollama").execute(None)
    result = await GetToolSelectorModelCommand().execute(None)
    assert result == {"model": "selector-small", "provider": "ollama"}
    assert repo.load("preferences", use_cache=False)["unrelated"] == "preserved"
    assert ConfigManager.get("local_models.supervisor.name") == "chat-main"
    refreshed = configured_selector(registry)
    assert refreshed._model_id == "selector-small"
    assert refreshed._base_url == "http://localhost:11434/v1"
    assert configured_selector(registry) is refreshed
    assert not await SetToolSelectorModelCommand(" ").execute(None)
