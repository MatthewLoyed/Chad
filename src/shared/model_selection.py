"""Resolve the active model without inventing a choice during local onboarding."""
from typing import Any, Callable


def resolve_selector_model(get: Callable[..., Any], default: str) -> str:
    """Keep one-model setup aligned when the main model later changes."""
    configured = get("tools.llm_selector_model", default)
    if get("setup.share_main_model", False) and get("local_models.use_local", False):
        return resolve_active_model(get, use_local=True) or configured
    return configured


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
