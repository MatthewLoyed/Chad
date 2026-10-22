"""Telegram control-lane helpers (OpenClaw-style /stop that bypasses the turn queue)."""

from __future__ import annotations

import re
from typing import Optional

# Bare /stop, /cancel, optional @BotName, optional trailing args.
_STOP_CMD = re.compile(
    r"^/(?:stop|cancel)(?:@[A-Za-z0-9_]+)?(?:\s|$)",
    re.IGNORECASE,
)


def is_telegram_stop_command(text: Optional[str]) -> bool:
    """True for /stop, /cancel (with optional @bot), or bare stop/cancel."""
    t = (text or "").strip()
    if not t:
        return False
    if _STOP_CMD.match(t):
        return True
    return t.lower() in ("stop", "cancel")
