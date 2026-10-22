"""Provider lifecycle strategies — one per local inference backend.

``get_provider_for(base_url)`` resolves the right :class:`ProviderLifecycle` for an
endpoint so ``model_swap.py`` / ``model_lifecycle.py`` never branch on the backend.
Resolution order:

1. **Config match** — if some ``local_models.providers.<name>`` entry has this exact
   ``base_url``, use that entry's ``provider`` key (if set) or its ``<name>``.
2. **URL heuristic** — ``:1234``/``lmstudio`` -> LM Studio, ``:11434``/``ollama`` -> Ollama,
   anything else -> a static single-model OpenAI endpoint (llama.cpp/vLLM).

Adding a new backend = one new :class:`ProviderLifecycle` subclass registered in
``_PROVIDER_CLASSES`` — no orchestration edits.
"""

from __future__ import annotations

import threading
from typing import Dict, Type

from src.core.intelligence.providers.base import (
    ProviderLifecycle,
    api_root,
    clean_model_id,
    logger,
    same_model,
)
from src.core.intelligence.providers.lmstudio import LMStudioLifecycle
from src.core.intelligence.providers.ollama import OllamaLifecycle
from src.core.intelligence.providers.static_endpoint import StaticEndpointLifecycle

__all__ = [
    "ProviderLifecycle",
    "LMStudioLifecycle",
    "OllamaLifecycle",
    "StaticEndpointLifecycle",
    "get_provider_for",
    "resolve_provider_name",
    "clean_model_id",
    "same_model",
    "api_root",
]

_PROVIDER_CLASSES: Dict[str, Type[ProviderLifecycle]] = {
    "lmstudio": LMStudioLifecycle,
    "ollama": OllamaLifecycle,
    "static": StaticEndpointLifecycle,
    # common aliases for "any OpenAI-compatible single-model server"
    "llamacpp": StaticEndpointLifecycle,
    "llama.cpp": StaticEndpointLifecycle,
    "vllm": StaticEndpointLifecycle,
    "openai": StaticEndpointLifecycle,
}

_instances: Dict[str, ProviderLifecycle] = {}
_instances_lock = threading.Lock()


def _provider_from_config(base_url: str) -> str | None:
    """If a configured ``local_models.providers.<name>`` entry owns this base_url, return
    its declared provider type (its optional ``provider`` key, else the entry name)."""
    try:
        from src.shared.config_manager import ConfigManager

        providers = ConfigManager.get("local_models.providers", {}) or {}
    except Exception:  # noqa: BLE001 — config not loaded yet (early boot / tests)
        return None
    if not isinstance(providers, dict):
        return None
    for entry_name, cfg in providers.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("base_url", "").rstrip("/") == base_url.rstrip("/"):
            declared = str(cfg.get("provider") or entry_name).lower()
            if declared in _PROVIDER_CLASSES:
                return declared
            return entry_name.lower() if entry_name.lower() in _PROVIDER_CLASSES else "static"
    return None


def resolve_provider_name(base_url: str) -> str:
    """Return the provider key (``lmstudio``/``ollama``/``static``) for a base_url."""
    from_cfg = _provider_from_config(base_url)
    if from_cfg:
        return from_cfg
    url = (base_url or "").lower()
    if "1234" in url or "lmstudio" in url or "lm-studio" in url or "lm_studio" in url:
        return "lmstudio"
    if "11434" in url or "ollama" in url:
        return "ollama"
    return "static"


def get_provider_for(base_url: str) -> ProviderLifecycle:
    """Resolve (and cache) the :class:`ProviderLifecycle` for ``base_url``."""
    with _instances_lock:
        inst = _instances.get(base_url)
        if inst is None:
            name = resolve_provider_name(base_url)
            cls = _PROVIDER_CLASSES.get(name, StaticEndpointLifecycle)
            inst = cls(base_url)
            _instances[base_url] = inst
            logger.debug(f"[ PROVIDERS ] Resolved '{base_url}' -> {name} ({cls.__name__})")
        return inst
