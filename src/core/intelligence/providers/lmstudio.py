"""LM Studio lifecycle strategy — native ``/api/v1/models/*`` management.

The load/unload logic here is lifted verbatim from the original ``model_swap.py`` so
LM Studio behavior is byte-for-byte unchanged by the provider refactor. Load/unload
require LM Studio's native ``/api/v1/models`` endpoints, keyed by an ``instance_id``
that only the native ``GET /api/v1/models`` exposes.

Residency also has to come from that native endpoint, NOT the inherited OpenAI-compat
``GET /v1/models`` (refs #592). LM Studio's ``/v1/models`` lists every *downloaded*
model, not the resident ones — the same "downloaded != loaded" trap ``ollama.py``
already sidesteps with ``/api/ps``. Inheriting the base implementation made
``list_loaded()`` return all 18 local models, so every residency check in the system
answered "yes, already loaded" and:

- ``load()`` below short-circuited on its idempotence guard and never issued a real
  ``/api/v1/models/load``, so every model JIT-loaded at LM Studio's saved UI default
  context length — precisely the failure the ``context_length`` comment warns about;
- ``model_lifecycle._ensure_resident_impl`` returned True immediately, so the whole
  coordinator (claims, ``_make_room``, VRAM-budget eviction) was inert on LM Studio;
- ``wait_theater/narrator.py``'s "only use the 9B when it is ALREADY resident" guard
  always passed, so a cosmetic status-line request raced the tool selector onto a
  cold 9B, JIT-triggered a load, then hit its own 8s timeout. LM Studio treats that
  client disconnect as "abort engine startup", killing the load the selector was
  waiting on ("Engine protocol startup was aborted" / "Model is unloaded") and forcing
  a full reload. That cost ~25-30s twice per turn — the bulk of the 68s `live_time`
  latency that made the live eval lanes unusable as a gate.
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


def _configured_context_length(model_id: str) -> Optional[int]:
    """Configured LM Studio load context_length for the active main model, if `model_id`
    IS that model. Lets any load path (swap-back, preload) inherit the right window without
    every caller having to thread it. Returns None for other models (they keep LM Studio's
    default) and on any config error (best-effort — never block a load)."""
    try:
        from src.shared.config_manager import ConfigManager
        roles = [ConfigManager.get_local_model_config() or {},
                 ConfigManager.get("local_models.secondary_model") or {},
                 ConfigManager.get("tools.selector_model") or {}]
        for role in roles:
            name, cl = role.get("name"), role.get("context_length")
            if name and cl and clean_model_id(name) == clean_model_id(model_id):
                return int(cl)
    except Exception:  # noqa: BLE001
        return None
    return None


class LMStudioLifecycle(ProviderLifecycle):
    name = "lmstudio"
    supports_explicit_load = True
    supports_unload = True

    async def _native_models(self, timeout: float = 5.0) -> Optional[List[Dict[str, Any]]]:
        """Entries from native ``GET /api/v1/models``, which is the only endpoint that
        reports residency and instance ids.
        Shape: ``{"models": [{"key": "...", "loaded_instances": [{"id": "..."}]}]}``.

        Returns ``None`` when the probe FAILED, versus ``[]`` for a successful probe that
        found nothing. Callers that are about to load must tell those apart: treating an
        unreachable backend as "nothing is resident" would skip ``load()``'s duplicate
        guard and risk a second instance of an already-resident model.
        """
        root = api_root(self.base_url)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{root}/api/v1/models")
                if resp.status_code != 200:
                    logger.debug(f"[ LMSTUDIO ] /api/v1/models returned HTTP {resp.status_code}")
                    return None
                body: Dict[str, Any] = resp.json()
        except Exception as e:  # noqa: BLE001 — liveness probe, never fatal
            logger.debug(f"[ LMSTUDIO ] Could not list native model instances: {e}")
            return None
        return [e for e in body.get("models", []) if isinstance(e, dict)]

    async def list_loaded(self, timeout: float = 5.0) -> List[str]:
        """Resident model ids — models with at least one ``loaded_instances`` entry.

        Deliberately NOT the inherited ``GET /v1/models``, which on LM Studio lists every
        downloaded model regardless of residency (see the module docstring).
        """
        return self._resident_keys(await self._native_models(timeout) or [])

    @staticmethod
    def _resident_keys(entries: List[Dict[str, Any]]) -> List[str]:
        """Ids of entries with at least one loaded instance."""
        return [
            str(entry.get("key"))
            for entry in entries
            if entry.get("key") and (entry.get("loaded_instances") or [])
        ]

    async def _resolve_instance_id(self, model_id: str) -> Optional[str]:
        """The ``instance_id`` of a resident ``model_id`` — required for unload, which the
        OpenAI-compat ``/v1/models`` doesn't expose."""
        for entry in await self._native_models() or []:
            if not same_model(str(entry.get("key", "")), model_id):
                continue
            for instance in entry.get("loaded_instances") or []:
                instance_id = instance.get("id") if isinstance(instance, dict) else None
                if instance_id:
                    return instance_id
        return None

    async def unload(self, model_id: str) -> bool:
        instance_id = await self._resolve_instance_id(model_id)
        if not instance_id:
            logger.debug(f"[ LMSTUDIO ] No loaded instance found for '{model_id}'; skipping unload.")
            return False
        root = api_root(self.base_url)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{root}/api/v1/models/unload", json={"instance_id": instance_id}
                )
                if resp.status_code == 200:
                    logger.info(f"[ LMSTUDIO ] Unloaded '{model_id}' (instance {instance_id}) to free VRAM.")
                    return True
                logger.debug(f"[ LMSTUDIO ] Unload returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[ LMSTUDIO ] Unload request failed (non-fatal): {e}")
        return False

    async def load(
        self, model_id: str, timeout: float = 180.0, context_length: Optional[int] = None
    ) -> bool:
        """Blocking load (LM Studio holds the connection until ready). Idempotent: if the
        model is already loaded we skip the load entirely — issuing a second
        ``/models/load`` for an already-resident (or concurrently JIT-loading) heavy model
        makes LM Studio spin up a *second* instance, and two 26B models at once will
        OOM/crash the box.

        ``context_length``: without an explicit value, LM Studio JIT-loads with whatever
        context length was last saved for that model in its UI (often a small default).
        """
        import os
        if os.getenv("CHAD_TESTING") == "true":
            try:
                from src.shared.config_manager import ConfigManager
                allowed = ConfigManager.get_llm_selector_model()
            except Exception:
                allowed = "qwen2.5-3b-instruct"
            if allowed and not same_model(model_id, allowed):
                logger.warning(
                    f"[ LMSTUDIO ] EVAL HARD GATE refused /models/load of '{model_id}' "
                    f"(only selector '{allowed}' allowed under CHAD_TESTING)."
                )
                return False

        # Residency must be read from a probe we know SUCCEEDED. `list_loaded()` returns []
        # both for "nothing resident" and for "the native endpoint did not answer", and
        # those must not be conflated here: treating an unreachable probe as "nothing
        # loaded" would skip the guard below and issue a load for an already-resident
        # model, which makes LM Studio spin up a SECOND instance — two 26B models at once
        # OOMs the box (refs #592, and the double-load incident this guard exists for).
        entries = await self._native_models()
        if entries is None:
            logger.warning(
                f"[ LMSTUDIO ] Could not confirm residency before loading '{model_id}' "
                f"(native model listing unavailable) — skipping load rather than risk a "
                f"duplicate instance."
            )
            return False
        if any(same_model(m, model_id) for m in self._resident_keys(entries)):
            logger.debug(f"[ LMSTUDIO ] '{model_id}' already loaded; skipping redundant load.")
            return True

        root = api_root(self.base_url)
        clean_id = clean_model_id(model_id)
        # If no explicit context_length was threaded through (many call sites — swap-back,
        # preload — historically forgot to), fall back to the configured value for THIS model.
        # Otherwise LM Studio JIT-loads the main model at its tiny UI default (~25k), which
        # silently under-sizes the supervisor's window and — because the token budgeter packs
        # to the model's declared context_window — overflows it into context rot / infinite
        # loops on long plan-execution turns. See model_router.py's recovery-load comment.
        # MUST be the cleaned id: callers hand us whatever the router/LiteLLM uses, which
        # carries a provider prefix (`openai/google/gemma-4-26b-a4b-qat`). LM Studio rejects
        # that with `model_not_found`, so the explicit load silently failed and the model came
        # back via JIT instead — outside the coordinator, and therefore without `_make_room`
        # ever running for it. `clean_id` was already computed here but never used (refs #592).
        payload: Dict[str, Any] = {"model": clean_id}
        if not context_length:
            context_length = _configured_context_length(model_id)
        if context_length:
            payload["context_length"] = context_length
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{root}/api/v1/models/load", json=payload)
                if resp.status_code == 200:
                    logger.info(f"[ LMSTUDIO ] Reloaded '{model_id}'.")
                    return True
                logger.debug(f"[ LMSTUDIO ] Load returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[ LMSTUDIO ] Load request failed (non-fatal): {e}")
        return False
