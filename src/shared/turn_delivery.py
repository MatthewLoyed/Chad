"""Turn delivery channel — who consumes orchestration stream events."""

from typing import Optional

DELIVERY_GUI = "gui"
DELIVERY_VOICE = "voice"
DELIVERY_SILENT = "silent"
DELIVERY_TELEGRAM = "telegram"

_VALID = frozenset({DELIVERY_GUI, DELIVERY_VOICE, DELIVERY_SILENT, DELIVERY_TELEGRAM})


def resolve_delivery(
    category: Optional[str] = None,
    explicit: Optional[str] = None,
) -> str:
    """Map category + optional client hint to a delivery channel."""
    if explicit in _VALID:
        return explicit
    if category == "voice":
        return DELIVERY_VOICE
    if category in ("contact", "telegram_agent"):
        return DELIVERY_TELEGRAM
    return DELIVERY_GUI


def is_gui_delivery(delivery: Optional[str]) -> bool:
    """True if delivery is targeting GUI (desktop/web/PWA)."""
    if not delivery:
        return True
    return delivery == DELIVERY_GUI


def is_telegram_delivery(delivery: Optional[str]) -> bool:
    """True if delivery is dedicated to Telegram."""
    return delivery == DELIVERY_TELEGRAM


def lock_key(session_id: str, delivery: str) -> str:
    """Orchestrator lock scope — voice, telegram, and GUI may run concurrently on one machine."""
    return f"{session_id}:{delivery}"

