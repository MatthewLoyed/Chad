"""Local-model load/unload primitives — a thin, provider-agnostic façade.

Historically this module hard-coded LM Studio's native ``/api/v1/models/load|unload``
API (see #450). That logic now lives in per-backend strategies under
``src/core/intelligence/providers/`` (LM Studio, Ollama, static llama.cpp/vLLM), and this
module just resolves the right provider for a ``base_url`` and delegates. LM Studio
behavior is unchanged; other OpenAI-compatible backends are first-class.

Public surface (used by ``model_lifecycle.py`` and the sync ``llm_selector.py`` path) is
kept identical — ``get_loaded_model_ids`` / ``load_model`` / ``unload_model`` — so no
caller changes. New code must still go through ``model_lifecycle.ensure_resident`` /
``release`` rather than calling these directly.
"""

from __future__ import annotations

from typing import List, Optional

from src.core.intelligence.providers import (
    api_root,  # noqa: F401 — re-exported for back-compat (was defined here)
    clean_model_id,  # noqa: F401 — re-exported for back-compat
    get_provider_for,
    same_model,  # noqa: F401 — re-exported for back-compat
)
from src.shared.logger import setup_logger

logger = setup_logger("ModelSwap")

# Back-compat private aliases: a few call sites / tests import these underscore names.
_api_root = api_root
_clean_model_id = clean_model_id
_same_model = same_model


async def get_loaded_model_ids(base_url: str) -> List[str]:
    """Ids of models currently resident/serving on the backend at ``base_url``."""
    return await get_provider_for(base_url).list_loaded()


async def unload_model(base_url: str, model_id: str) -> bool:
    """Unload a model by name. Non-fatal on any failure (returns ``False``)."""
    return await get_provider_for(base_url).unload(model_id)


async def load_model(
    base_url: str, model_id: str, timeout: float = 180.0, context_length: Optional[int] = None
) -> bool:
    """Load a model by name. Idempotent (skips if already resident) and non-fatal — worst
    case the model JIT-loads on the next inference call anyway."""
    return await get_provider_for(base_url).load(
        model_id, timeout=timeout, context_length=context_length
    )
