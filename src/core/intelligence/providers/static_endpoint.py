"""Static single-model endpoint strategy — llama.cpp ``server`` / vLLM / any plain
OpenAI-compatible endpoint that serves ONE model with no load/unload API.

Residency is whatever the server was started with, so:

- ``load`` is a no-op returning ``True`` (the model is already "resident" by definition;
  worst case the endpoint simply serves whatever it has).
- ``unload`` is a no-op returning ``False`` (nothing to evict — reported as "did not
  unload" so the VRAM coordinator doesn't believe it freed anything).
- ``list_loaded`` uses the inherited OpenAI-compat ``GET /v1/models`` (which lists the
  single served model).

Because ``supports_unload``/``supports_explicit_load`` are ``False``, the lifecycle
coordinator's VRAM-budget eviction and JIT-load logic degrade to no-ops here instead of
firing HTTP calls the backend can't answer.
"""

from __future__ import annotations

from typing import Optional

from src.core.intelligence.providers.base import ProviderLifecycle, logger


class StaticEndpointLifecycle(ProviderLifecycle):
    name = "static"
    supports_explicit_load = False
    supports_unload = False

    async def load(
        self, model_id: str, timeout: float = 180.0, context_length: Optional[int] = None
    ) -> bool:
        logger.debug(
            f"[ STATIC ] '{model_id}' — static endpoint has no explicit load; treating as resident."
        )
        return True

    async def unload(self, model_id: str) -> bool:
        logger.debug(f"[ STATIC ] '{model_id}' — static endpoint has no unload; no-op.")
        return False
