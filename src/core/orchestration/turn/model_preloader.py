"""Supervisor-model residency, resolved before the graph is invoked.

The 9B tool-selector load can evict the larger supervisor model from VRAM. If we
let the supervisor JIT-reload on its first call, the user gets tens of seconds of
unattributed dead air — and worse, LM Studio's Auto-Evict can kill a
still-loading selector mid-swap (#592). So the load happens here, up front,
behind a visible "Loading …" status.

Local (LM Studio) adapters only, and non-fatal throughout: worst case the model
JIT-loads on the next call exactly as it did before.
"""

from __future__ import annotations

import time
from typing import Any, Optional, Tuple

from src.shared.logger import setup_logger
from src.core.orchestration.turn.status_emitter import TurnStatusEmitter

logger = setup_logger("TurnModelPreloader")


class TurnModelPreloader:
    """Ensures the main/supervisor model is resident before invocation."""

    def __init__(self, emitter: Optional[TurnStatusEmitter] = None):
        self._emitter = emitter

    @staticmethod
    def resolve_local_target(adapter: Any) -> Tuple[Optional[str], Optional[str]]:
        """``(base_url, model_id)`` for a local backend, unwrapping wrapper models.

        The active adapter is not always the ``LiteLLMAdapter`` that actually knows the
        endpoint: ``model_router`` hands back a ``LiteLLMModel``, a ``BaseModel`` wrapper
        that keeps the real adapter on ``.adapter`` and exposes no ``base_url`` of its own.
        Reading ``base_url`` straight off the wrapper therefore yielded ``None``, and the
        main-model preload silently no-op'd on every supervisor turn (refs #592) — so the
        supervisor model JIT-loaded on first inference, which is exactly what lets LM
        Studio's Auto-Evict kill a still-loading tool-selector model.
        """
        node, depth = adapter, 0
        while node is not None and depth < 4:      # depth cap: wrappers are shallow
            base_url = getattr(node, "base_url", None)
            model_id = getattr(node, "model_name", None)
            if base_url and model_id:
                return base_url, model_id
            node = getattr(node, "adapter", None)
            depth += 1
        return None, None

    async def preload(self, adapter: Any, delivery: Optional[str] = None) -> None:
        """Make the supervisor's model resident, narrating the gap if a load is needed."""
        base_url, model_id = self.resolve_local_target(adapter)
        is_local = "localhost" in str(base_url) or "127.0.0.1" in str(base_url)
        if not base_url or not model_id or not is_local:
            # Cloud adapters have no residency to manage — skipping is correct and routine,
            # so it stays at debug. A LOCAL adapter we failed to resolve is the #592 bug
            # (silent no-op -> the supervisor model JIT-loads -> LM Studio's Auto-Evict
            # kills the still-loading selector), and that must be loud.
            from src.shared.config_manager import ConfigManager
            if ConfigManager.is_local_model_active():
                logger.warning(
                    f"[ TURN ] Main-model preload SKIPPED for a LOCAL turn — "
                    f"adapter={type(adapter).__name__} base_url={base_url!r} "
                    f"model={model_id!r}. The supervisor model will JIT-load on first use."
                )
            else:
                logger.debug(
                    f"[ TURN ] Main-model preload not applicable "
                    f"(adapter={type(adapter).__name__}, base_url={base_url!r})."
                )
            return
        try:
            from src.core.intelligence.model_swap import get_loaded_model_ids
            from src.core.intelligence.model_lifecycle import (
                ensure_resident, current_priority, OWNER_MAIN_MODEL,
            )
            from src.core.intelligence.providers.base import same_model
            priority = current_priority()
            loaded = await get_loaded_model_ids(base_url)
            # MUST be same_model, not a raw compare (refs #592): `model_id` here is the
            # adapter's LiteLLM name and carries a provider prefix
            # (`openai/google/gemma-4-26b-a4b-qat`), while the backend reports the bare id.
            # A `==` between the two is never true, so this always took the "not resident"
            # branch and mis-attributed the status even when the model was already up.
            already = any(same_model(m, str(model_id)) for m in loaded)
            logger.info(
                f"[ TURN ] Main-model preload check: '{model_id}' "
                f"{'resident' if already else 'NOT resident'} (loaded={loaded})"
            )
            if already:
                await ensure_resident(base_url, model_id, owner=OWNER_MAIN_MODEL, priority=priority)
                return  # already resident — no reload needed
            short = str(model_id).split("/")[-1]
            if self._emitter:
                self._emitter.status(f"Loading {short}…", delivery)
            logger.warning(
                f"[ TURN ] Supervisor model '{model_id}' not resident — preloading before invoke."
            )
            t0 = time.time()
            await ensure_resident(base_url, model_id, owner=OWNER_MAIN_MODEL, priority=priority)
            logger.warning(f"[ TURN ] Supervisor model preload took {time.time() - t0:.1f}s.")
        except Exception as e:
            logger.debug(f"[ TURN ] Main-model preload skipped (non-fatal): {e}")


__all__ = ["TurnModelPreloader"]
