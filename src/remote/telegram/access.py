"""
Telegram inbound access control.

Fail-closed allowlist: with no allowed_user_ids / allowed_chat_ids configured,
inbound messages are rejected (unless pair_on_first_message is explicitly true,
in which case the first sender is persisted into the allowlist).
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("TelegramAccess")

UNAUTHORIZED_REPLY = (
    "This Chad bot is private. Your Telegram account is not on the allowlist."
)


def _as_int_list(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, (int, float, str)):
        raw: Sequence[Any] = [value]
    elif isinstance(value, Iterable):
        raw = list(value)
    else:
        return []
    out: List[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def load_allowlists() -> Tuple[List[int], List[int]]:
    """Return (allowed_user_ids, allowed_chat_ids) from config/secrets."""
    users = _as_int_list(ConfigManager.get_secret("telegram.allowed_user_ids", []))
    chats = _as_int_list(ConfigManager.get_secret("telegram.allowed_chat_ids", []))
    return users, chats


def pair_on_first_message_enabled() -> bool:
    val = ConfigManager.get_secret("telegram.pair_on_first_message", False)
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)


def is_authorized(user_id: Optional[int], chat_id: Optional[int]) -> bool:
    """True if the sender matches the allowlist (fail-closed when empty)."""
    users, chats = load_allowlists()
    if not users and not chats:
        return False
    if user_id is not None and int(user_id) in users:
        return True
    if chat_id is not None and int(chat_id) in chats:
        return True
    return False


def try_pair_first_message(user_id: Optional[int], chat_id: Optional[int]) -> bool:
    """
    If allowlists are empty and pair_on_first_message is enabled, persist this
    sender as the owner and return True. Otherwise return False.
    """
    if user_id is None and chat_id is None:
        return False
    users, chats = load_allowlists()
    if users or chats:
        return False
    if not pair_on_first_message_enabled():
        return False

    seed_users = [int(user_id)] if user_id is not None else []
    seed_chats = [int(chat_id)] if chat_id is not None else []
    try:
        from src.infrastructure.repositories.config_repository import ConfigRepository

        repo = ConfigRepository()
        if seed_users:
            repo.update_secret("telegram.allowed_user_ids", seed_users)
        if seed_chats:
            repo.update_secret("telegram.allowed_chat_ids", seed_chats)
        repo.update_secret("telegram.pair_on_first_message", False)
        # Refresh in-process config so subsequent messages in this process see the list.
        ConfigManager._loaded = False
        ConfigManager.load_config()
        logger.warning(
            "[ TELEGRAM ] Paired first sender user_id=%s chat_id=%s into allowlist",
            user_id,
            chat_id,
        )
        return True
    except Exception as e:
        logger.error(f"[ TELEGRAM ] Failed to persist first-message pairing: {e}", exc_info=True)
        return False


def authorize_inbound(user_id: Optional[int], chat_id: Optional[int]) -> bool:
    """Authorize, optionally pairing the first sender when configured."""
    if is_authorized(user_id, chat_id):
        return True
    if try_pair_first_message(user_id, chat_id):
        return is_authorized(user_id, chat_id)
    return False
