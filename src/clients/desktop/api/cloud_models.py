"""Filter cloud catalogue entries by providers with configured credentials."""
from src.shared.config_manager import ConfigManager


def available_cloud_models():
    from src.core.intelligence.provider_failover import scan_available_providers
    providers = set(scan_available_providers())
    if "claude" in providers:
        providers.add("anthropic")
    catalogue = ConfigManager.get("available_cloud_models") or []

    def configured(model):
        name = str(model.get("id", "")).lower()
        prefix = name.split("/", 1)[0]
        if "/" in name:
            provider = {"google": "gemini", "claude": "anthropic"}.get(prefix, prefix)
        elif name.startswith("gemini"):
            provider = "gemini"
        elif name.startswith("claude"):
            provider = "anthropic"
        elif name.startswith(("gpt", "o1", "o3", "o4")):
            provider = "openai"
        else:
            return False
        return provider in providers

    return [model for model in catalogue if isinstance(model, dict) and configured(model)]
