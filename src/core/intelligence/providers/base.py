"""Provider lifecycle strategy — the interface every local backend implements.

Historically ``model_swap.py`` baked LM Studio's *native* model-management API
(``GET/POST /api/v1/models/load|unload`` keyed by ``instance_id``) straight into the
load/unload primitives the whole lifecycle coordinator (``model_lifecycle.py``) depends
on. No other OpenAI-compatible backend speaks that dialect:

- **LM Studio** — native ``/api/v1/models/*`` with instance IDs (see :mod:`.lmstudio`).
- **Ollama** — models load on first request; residency is controlled with ``keep_alive``
  and inspected via ``/api/ps`` (see :mod:`.ollama`).
- **llama.cpp server / vLLM** — serve a *single* resident model with **no** load/unload
  API at all; load/unload must be no-ops (see :mod:`.static_endpoint`).

Each backend gets a :class:`ProviderLifecycle` implementation exposing the same three
primitives the coordinator needs — ``list_loaded`` / ``load`` / ``unload`` — plus
capability flags so callers can reason about a backend without ``isinstance`` checks.
``model_swap.py`` is now a thin façade that resolves the right provider for a base_url
and delegates. **Provider-specific HTTP lives here, never in the coordinator.**
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

import httpx

from src.shared.logger import setup_logger

logger = setup_logger("ProviderLifecycle")

# Model-id prefixes LiteLLM/router attach that aren't part of the backend's own id.
_KNOWN_PREFIXES = ("ollama/", "openai/", "lm_studio/", "huggingface/", "vllm/", "gemini/")


def clean_model_id(model_id: str) -> str:
    """Strip a router/LiteLLM provider prefix (e.g. ``ollama/qwen`` -> ``qwen``)."""
    for prefix in _KNOWN_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


def same_model(a: str, b: str) -> bool:
    """Compare two model ids ignoring provider prefix and ``:quant`` suffix
    (e.g. ``qwen/qwen3.5-9b`` == ``qwen/qwen3.5-9b:Q4_K_M``)."""
    a_clean = clean_model_id(a).split(":")[0]
    b_clean = clean_model_id(b).split(":")[0]
    return a_clean == b_clean


def api_root(base_url: str) -> str:
    """Strip the OpenAI-compat ``/v1`` suffix to reach a backend's native API root."""
    return base_url.replace("/v1", "").rstrip("/")


class ProviderLifecycle(ABC):
    """Per-backend load/unload/residency strategy bound to a single ``base_url``.

    Subclasses override only what differs; :meth:`list_loaded` defaults to the
    OpenAI-compat ``GET /v1/models`` listing, which is correct only for static
    single-model endpoints. Both real managed backends override it, because on both
    ``/v1/models`` lists what is *available* rather than what is *resident*: Ollama uses
    ``/api/ps``, LM Studio uses ``/api/v1/models`` + ``loaded_instances`` (refs #592).
    Any new managed backend must assume it needs an override too.
    """

    #: Stable provider identifier (also used as the config key under
    #: ``local_models.providers.<name>``).
    name: str = "openai"

    #: ``False`` for backends that have no load/unload API (llama.cpp/vLLM): a load
    #: request is a no-op and residency is whatever the server was started with.
    supports_explicit_load: bool = True
    #: ``False`` for backends that can't evict on demand — the coordinator must not
    #: rely on unload to free VRAM there.
    supports_unload: bool = True

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def list_loaded(self, timeout: float = 5.0) -> List[str]:
        """Return the ids of models currently *resident/serving* on this backend.

        Default: OpenAI-compat ``GET /v1/models``. Non-fatal on any error (returns
        ``[]``) so a down server never raises into the lifecycle coordinator.
        """
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{self.base_url.rstrip('/')}/models")
                if resp.status_code == 200:
                    return [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
        except Exception as e:  # noqa: BLE001 — liveness probe, never fatal
            logger.debug(f"[ {self.name.upper()} ] Could not list models: {e}")
        return []

    @abstractmethod
    async def load(
        self, model_id: str, timeout: float = 180.0, context_length: Optional[int] = None
    ) -> bool:
        """Make ``model_id`` resident. Idempotent and non-fatal — worst case the model
        JIT-loads on the next inference call anyway. Returns ``True`` on success (or when
        already resident / when the backend has no explicit-load concept)."""

    @abstractmethod
    async def unload(self, model_id: str) -> bool:
        """Evict ``model_id`` to free VRAM. Non-fatal; returns ``True`` when the model was
        actually unloaded, ``False`` otherwise (including when unload is unsupported)."""
