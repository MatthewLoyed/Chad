from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Dict, List, Optional, Tuple

from src.shared.logger import setup_logger

logger = setup_logger("TurnModeResolver")

# Categories that name a whole delivery lane the caller chose on purpose, rather
# than something inferred from the prompt. These survive intent resolution.
EXPLICIT_LANE_CATEGORIES = frozenset({"voice", "contact", "telegram_agent"})


class TurnModeResolver:
    """Decides whether a turn runs as ``chat`` / ``agent`` / ``plan``.

    Owns the lazily-built :class:`IntentModeRouter` used for automatic
    classification, and the explicit overrides (voice, multimodal, system turns,
    manual category) that bypass the router.
    """

    def __init__(self) -> None:
        self._intent_router = None

    def get_intent_router(self):
        if self._intent_router is None:
            from src.core.intelligence.intent_mode_router import IntentModeRouter

            self._intent_router = IntentModeRouter.from_config()
        return self._intent_router

    async def warmup(self) -> None:
        """Warm the intent router (offloaded to a thread — it loads a local model).

        Skipped when ``intent_router.skip_warmup`` is true — the router loads on demand
        only for actual "auto" turns, which avoids burning VRAM for gemma at startup.
        """
        from src.shared.config_manager import ConfigManager
        ir_cfg = ConfigManager.get("intent_router") or {}
        if ir_cfg.get("skip_warmup", False):
            logger.info("[ TURN ] Intent router warmup skipped (skip_warmup=true).")
            return
        router = self.get_intent_router()
        if router:
            logger.info("[ TURN ] Warming intent mode router...")
            await asyncio.to_thread(router.warmup)

    @staticmethod
    def category_for_turn_mode(turn_mode: str) -> str:
        from src.shared.turn_mode import category_for_mode, normalize_turn_mode

        return category_for_mode(normalize_turn_mode(turn_mode))

    @staticmethod
    def category_for_turn(incoming_category: Optional[str], turn_mode: str) -> str:
        """Resolve the turn's category, preserving explicitly-selected lanes.

        ``turn_mode`` only spans chat/agent/plan, so deriving the category from it
        discards a lane the client picked deliberately. That silently rewrote
        ``category="voice"`` to ``"agent"`` before the graph state was built, which
        made ``route_entry`` skip the fast voice responder entirely and run every
        spoken turn through the full supervisor + tool-selection pipeline.
        """
        if incoming_category in EXPLICIT_LANE_CATEGORIES:
            return str(incoming_category)
        return TurnModeResolver.category_for_turn_mode(turn_mode)

    async def resolve(
        self,
        *,
        prompt: str,
        category: str,
        content_blocks: Optional[list],
        image_base64: Optional[str],
        history: List[Dict[str, Any]],
        initiator: Optional[str],
        platform: Optional[str],
    ) -> Tuple[str, str, Optional[float]]:
        """Return ``(turn_mode, source, confidence)`` for this turn.

        Explicit overrides win in priority order; otherwise the intent router
        classifies the prompt against recent history.
        """
        from src.shared.turn_mode import normalize_turn_mode

        manual_map = {
            "chat": "chat",
            "conversation": "chat",
            "agent": "agent",
            "planning": "plan",
            "plan": "plan",
        }

        if category == "voice":
            return "agent", "user_manual", None
        if category == "contact":
            # Phone front desk: conversational lane + escalate_to_agent (not the 1B intent router).
            return "chat", "user_manual", None
        if category == "telegram_agent":
            # Pocket-remote tool lane — keep agent turn_mode with fixed telegram tools.
            return "agent", "user_manual", None
        if category == "autonomous" or initiator == "system" or platform in ("system", "background", "eval"):
            # Heartbeat/cron/webhook turns already know their own purpose — running them
            # through the intent-router classifier is wasted latency AND an extra gemma-1b
            # load/classify on every tick, competing for VRAM with whatever the user's
            # interactive turn currently has resident. Eval/dry-run (platform="eval") same:
            # never burn a model load to re-classify.
            #
            # Honor an EXPLICIT harness/GUI category when one was supplied. Previously this
            # branch always returned "agent", which silently demoted category="planning"
            # workflow evals to the agent lane (HITL seed 2026-07-24: 0 plan steps despite
            # wedding_quality_hitl cases declaring category=planning).
            if category in manual_map:
                return manual_map[category], "system_turn_explicit", None
            return "agent", "system_turn", None

        if category in manual_map and category != "auto":
            return manual_map[category], "user_manual", None

        # Auto / unset category: attachments imply tool-capable turns.
        if image_base64 or content_blocks:
            return "agent", "multimodal_override", None

        router = self.get_intent_router()
        if router is None:
            return "agent", "router_disabled", None

        ctx = contextvars.copy_context()
        result = await asyncio.to_thread(ctx.run, router.classify, prompt, history)

        from src.shared.config_manager import ConfigManager
        ir_cfg = ConfigManager.get("intent_router") or {}

        canonical = normalize_turn_mode(result.mode)

        # Unload gemma after classification if configured — it served its purpose and
        # keeping it in VRAM alongside the main model measurably hurts token throughput.
        if ir_cfg.get("unload_after_use", False) and hasattr(router, "unload"):
            router.unload()  # fire-and-forget: starts its own daemon thread internally

        return canonical, "auto_router", result.confidence
