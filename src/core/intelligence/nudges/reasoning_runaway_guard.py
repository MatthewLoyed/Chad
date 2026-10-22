"""Trips when a model reasons past a token cap without producing a tool call."""

from typing import Optional

from .base import Nudge, NudgeContext, NudgeResult
from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager

logger = setup_logger("ReasoningRunawayGuard")

DEFAULT_TOKEN_CAP = 4000
DEFAULT_NUDGE_MESSAGE = (
    "You thought with no tool calls — stop reasoning and produce your output/next action now."
)


def _estimate_tokens(text: str) -> int:
    """Cheap chars/4 estimate — a watchdog threshold doesn't need an exact tokenizer."""
    return len(text) // 4


class ReasoningRunawayGuard(Nudge):
    """Fires once accumulated reasoning tokens exceed `token_cap` with no tool call seen."""

    name = "reasoning_runaway"

    def __init__(self, model_name: str = "", token_cap: Optional[int] = None):
        self._reasoning_chars = 0
        self._reasoning_tokens = 0
        self._tool_call_seen = False
        self.token_cap = token_cap if token_cap is not None else self._resolve_cap(model_name)

    @staticmethod
    def _resolve_cap(model_name: str) -> int:
        try:
            per_model = (ConfigManager.get("local_models.model_settings") or {}).get(model_name, {})
            override = per_model.get("reasoning_token_cap")
            if override is not None:
                return int(override)
        except Exception:
            pass
        try:
            return int(ConfigManager.get("planner.reasoning_token_cap", DEFAULT_TOKEN_CAP) or DEFAULT_TOKEN_CAP)
        except Exception:
            return DEFAULT_TOKEN_CAP

    def observe(self, context: NudgeContext) -> None:
        chunk = context.reasoning_chunk or (context.content_chunk if not context.has_tool_call and context.tools_available else "")
        if chunk:
            # Round after accumulation. Token-sized deltas shorter than four
            # characters must not repeatedly contribute zero to the watchdog.
            self._reasoning_chars += len(chunk)
            self._reasoning_tokens = self._reasoning_chars // 4
        if context.has_tool_call:
            self._tool_call_seen = True

    def check(self) -> Optional[NudgeResult]:
        if self.token_cap <= 0 or self._tool_call_seen:
            return None
        if self._reasoning_tokens < self.token_cap:
            return None
        logger.warning(
            f"[ REASONING_GUARD ] Runaway reasoning detected: ~{self._reasoning_tokens} "
            f"tokens (cap={self.token_cap}) with no tool call — triggering nudge."
        )
        return NudgeResult(
            message=DEFAULT_NUDGE_MESSAGE,
            disable_thinking=True,
            reason=f"reasoning_tokens>={self.token_cap}",
        )

    def reset(self) -> None:
        self._reasoning_chars = 0
        self._reasoning_tokens = 0
        self._tool_call_seen = False
