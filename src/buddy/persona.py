"""Thin persona slice for ambient buddy comments — not the full supervisor prompt."""

from __future__ import annotations

import re

from src.shared.logger import setup_logger

logger = setup_logger("BuddyPersona")

# Persona rows pulled from PromptRegistry (identity + user context only).
_PERSONA_IDS: tuple[str, ...] = ("identity", "user")
_MAX_CHARS_PER_BLOCK = 500
_MAX_TOTAL_CHARS = 900

_FALLBACK = (
    "You are Chad, the user's witty desktop buddy — a small purple pet on their screen. "
    "You know the user is a developer building you (Chad). "
    "Speak in first person as Chad. One short casual comment at a time."
)

_TASK_SUFFIX = (
    "\n\nMode: desktop buddy glance. You peeked at the user's screen. "
    "Reply with ONE short comment (max 20 words). First person as Chad. "
    "Warm, personal, lightly playful — reference what you know about them when it fits. "
    "No tool talk, no lists, no quotes around the line."
)


def _truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def build_buddy_persona_prompt() -> str:
    """Assemble a minimal system prompt from cloud persona rows + buddy task."""
    from src.infrastructure.repositories.prompt_registry import PromptRegistry

    registry = PromptRegistry.get_instance()
    if not getattr(registry, "_loaded", False):
        logger.debug("[ BUDDY ] PromptRegistry not warm — using fallback persona.")
        return _FALLBACK + _TASK_SUFFIX

    blocks: list[str] = []
    total = 0
    for pid in _PERSONA_IDS:
        try:
            content = registry.get_prompt(pid)
        except RuntimeError:
            continue
        if not content or not content.strip():
            continue
        chunk = _truncate(content, _MAX_CHARS_PER_BLOCK)
        if total + len(chunk) > _MAX_TOTAL_CHARS:
            remaining = _MAX_TOTAL_CHARS - total
            if remaining < 80:
                break
            chunk = _truncate(chunk, remaining)
        blocks.append(chunk)
        total += len(chunk)

    if not blocks:
        return _FALLBACK + _TASK_SUFFIX

    persona = "\n\n".join(blocks)
    return persona + _TASK_SUFFIX
