"""Resolve the active model without inventing a choice during local onboarding."""
from typing import Any, Callable


def describe_endpoint_model(config: dict, settings: dict | None = None) -> dict:
    """Describe explicit-endpoint routing without implying inference is on-device."""
    settings = settings or {}
    thinking = settings.get("thinking")
    return {"mode": "local", "hosting": config.get("hosting", "unknown"),
            "name": config.get("name") or "Choose a model",
            "provider": config.get("provider", "lmstudio"),
            "supports_tools": config.get("supports_tools", False),
            "tools_verified": config.get("tools_verified", False),
            "thinking_enabled": bool(thinking) if thinking is not None else None,
            "model_settings": settings}


def resolve_selector_model(get: Callable[..., Any], default: str) -> str:
    """Keep one-model setup aligned when the main model later changes."""
    configured = get("tools.llm_selector_model", default)
    dedicated = get("tools.selector_model") or {}
    if dedicated.get("name"):
        return dedicated["name"]
    if get("setup.share_main_model", False) and get("local_models.use_local", False):
        return resolve_active_model(get, use_local=True) or configured
    return configured


def selector_config():
    """Resolve a selector's identity, endpoint and credential as one role."""
    from src.shared.config_manager import ConfigManager as config
    dedicated = config.get("tools.selector_model")
    if isinstance(dedicated, dict) and dedicated.get("name"):
        return dedicated
    endpoint = (config.get_local_model_config() if config.get("setup.share_main_model", False)
                else config.get_local_provider_config("lmstudio"))
    return {**endpoint, "name": config.get_llm_selector_model()}


def secondary_config():
    from src.shared.config_manager import ConfigManager as config
    override = config.get("planner.scout_model")
    if override:
        return {**config.get_local_provider_config("lmstudio"), "name": override}
    if config.get("setup.secondary_policy") == "main":
        return config.get_local_model_config()
    selected = config.get("local_models.secondary_model")
    if isinstance(selected, dict) and selected.get("name"):
        return selected
    main = config.get_local_model_config()
    # Preserve an explicit legacy Scout override, never substitute the selector.
    return {**main, "name": config.get("planner.scout_model") or main.get("name")}


def resolve_active_model(get: Callable[..., Any], *, use_local: bool) -> str:
    """Prefer the user's model aliases; legacy cloud configuration may fall back."""
    active = (get("local_models.supervisor") or get("supervisor")
              or get("local_models.active_model") or get("active_model"))
    if active:
        return active.get("name", "") if isinstance(active, dict) else active

    # An unconfigured local installation can boot into setup. Never present the
    # legacy text adapter's model as a user selection.
    if use_local:
        return ""

    text_model = get("models.text.model")
    if text_model:
        return text_model
    raise RuntimeError(
        "CRITICAL ERROR: No active AI model configured. "
        "Please set 'supervisor' or 'active_model' in config/config.yaml or preferences.yaml."
    )
