"""Build and refresh the tool selector from effective Brain preferences."""
from src.shared.config_manager import ConfigManager
from .llm_selector import LLMToolSelector


def selector_configuration():
    from src.shared.model_selection import selector_config
    connection = selector_config()
    return {
        "model_id": ConfigManager.get_llm_selector_model(),
        "base_url": connection.get("base_url", "http://localhost:1234/v1").rstrip("/"),
        "strict_model": bool(ConfigManager.get("tools.llm_selector_strict_model", True)),
        "unload_after_select": bool(ConfigManager.get("tools.llm_selector_unload_after_select", True)),
        "context_length": int(ConfigManager.get("tools.llm_selector_context_length", 32768)),
    }


def configured_selector(registry):
    current = registry._llm_selector
    # Tests and integrations may inject another selector implementation.
    if not isinstance(current, LLMToolSelector):
        return current
    config = selector_configuration()
    if current._model_id != config["model_id"] or current._base_url != config["base_url"]:
        replacement = LLMToolSelector(**config)
        replacement.set_main_model_hint(current._main_model_hint)
        registry._llm_selector = replacement
    return registry._llm_selector
