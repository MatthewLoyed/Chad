"""Persist the independent local tool selector choice on the Brain."""
from dataclasses import dataclass

from .base_command import Command
from src.shared.config_manager import ConfigManager
from src.infrastructure.repositories import ConfigRepository


@dataclass
class GetToolSelectorModelCommand(Command):
    async def execute(self, facade):
        selected = ConfigManager.get("tools.selector_model") or {}
        return {"model": ConfigManager.get_llm_selector_model(),
                "provider": selected.get("provider", ConfigManager.get("tools.llm_selector_provider", "lmstudio"))}


@dataclass
class SetToolSelectorModelCommand(Command):
    model_id: str
    provider: str = "lmstudio"

    async def execute(self, facade):
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            return False
        if self.provider not in ("lmstudio", "ollama", "custom"):
            return False
        selected = {**ConfigManager.get_local_provider_config(self.provider),
                    "name": self.model_id.strip(), "provider": self.provider}
        patch = {"tools": {"llm_selector_model": self.model_id.strip(),
                           "llm_selector_provider": self.provider, "selector_model": selected}}
        repo = ConfigRepository()
        prefs = repo.load("preferences", use_cache=False)
        ConfigManager._recursive_update(prefs, patch)
        if not repo.save("preferences", prefs):
            return False
        ConfigManager._recursive_update(ConfigManager._config, patch)
        return True
