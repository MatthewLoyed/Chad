"""Ollama lifecycle strategy — residency via ``keep_alive``, inspection via ``/api/ps``.

Ollama has no explicit load/unload endpoints. Instead:

- **Load / keep resident:** ``POST /api/generate`` with an empty prompt and
  ``keep_alive: -1`` warms the model into VRAM and pins it there (``-1`` = keep
  indefinitely, matching how LM Studio models stay resident until evicted).
- **Unload:** the same endpoint with ``keep_alive: 0`` evicts immediately.
- **List loaded:** ``GET /api/ps`` lists *resident* models. (Ollama's OpenAI-compat
  ``GET /v1/models`` lists every *pulled* model, not just the resident ones, so the
  inherited default would wrongly report unloaded models as loaded — hence the override.)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from src.core.intelligence.providers.base import (
    ProviderLifecycle,
    api_root,
    clean_model_id,
    logger,
    same_model,
)


class OllamaLifecycle(ProviderLifecycle):
    name = "ollama"
    supports_explicit_load = True
    supports_unload = True

    async def list_loaded(self, timeout: float = 5.0) -> List[str]:
        """Resident models via native ``GET /api/ps`` (not OpenAI-compat ``/v1/models``,
        which lists all pulled models)."""
        root = api_root(self.base_url)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{root}/api/ps")
                if resp.status_code == 200:
                    body: Dict[str, Any] = resp.json()
                    return [
                        m.get("name") or m.get("model", "")
                        for m in body.get("models", [])
                        if isinstance(m, dict) and (m.get("name") or m.get("model"))
                    ]
                logger.debug(f"[ OLLAMA ] /api/ps returned HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[ OLLAMA ] Could not list resident models: {e}")
        return []

    async def _set_keep_alive(self, model_id: str, keep_alive: Any, timeout: float) -> bool:
        root = api_root(self.base_url)
        clean_id = clean_model_id(model_id)
        # Empty prompt => Ollama loads/unloads the model without generating tokens.
        payload = {"model": clean_id, "keep_alive": keep_alive, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{root}/api/generate", json=payload)
                if resp.status_code == 200:
                    return True
                logger.debug(f"[ OLLAMA ] keep_alive={keep_alive} returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[ OLLAMA ] keep_alive={keep_alive} request failed (non-fatal): {e}")
        return False

    async def load(
        self, model_id: str, timeout: float = 180.0, context_length: Optional[int] = None
    ) -> bool:
        loaded = await self.list_loaded()
        if any(same_model(m, model_id) for m in loaded):
            logger.debug(f"[ OLLAMA ] '{model_id}' already resident; skipping redundant load.")
            return True
        ok = await self._set_keep_alive(model_id, -1, timeout)
        if ok:
            logger.info(f"[ OLLAMA ] '{model_id}' resident (keep_alive=-1).")
        return ok

    async def unload(self, model_id: str) -> bool:
        ok = await self._set_keep_alive(model_id, 0, 30.0)
        if ok:
            logger.info(f"[ OLLAMA ] Unloaded '{model_id}' (keep_alive=0) to free VRAM.")
        return ok
