"""
Lean identity pack for the Telegram contact front desk.

Hot-path only: Telegram peer name + a few durable facts. Full memory stays on
the escalated agent turn.
"""

from __future__ import annotations

from typing import Any, Optional

from src.shared.logger import setup_logger

logger = setup_logger("TelegramContactContext")


def build_contact_peer_block(session_key: Optional[str] = None, chat_id: Optional[int] = None) -> str:
    """Resolve first_name / username from telegram_chats for the system prompt."""
    try:
        from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository

        repo = TelegramChatRepository()
        chat = None
        if chat_id is not None:
            chat = repo.get_chat(int(chat_id))
        elif session_key:
            resolved = repo.get_chat_id(session_key)
            if resolved is not None:
                chat = repo.get_chat(int(resolved))
        if not chat:
            return ""

        name = (chat.first_name or "").strip()
        last = (chat.last_name or "").strip()
        if name and last:
            display = f"{name} {last}"
        else:
            display = name or (chat.username or "").strip() or "the user"
        uname = (chat.username or "").strip()
        lines = [
            "### TELEGRAM CONTACT",
            f"- Display name: {display}",
        ]
        if uname:
            lines.append(f"- Username: @{uname}")
        lines.append("- Style: Do NOT open with 'Hey [Name]!' or greeting formulas. Jump directly into your thought/reply like texting a friend.")
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Contact peer block unavailable: {e}")
        return ""


def build_lean_memory_lines(
    memory_engine: Optional[Any],
    user_input: Optional[str],
    history: Optional[list] = None,
    *,
    max_results: int = 5,
    domain: Optional[str] = None,
) -> str:
    """Short durable context for the contact desk (not full get_memory_block)."""
    if not memory_engine or not user_input:
        return ""
    try:
        if hasattr(memory_engine, "get_unified_context"):
            raw = memory_engine.get_unified_context(
                query=user_input, history=history, domain=domain
            )
        else:
            raw = memory_engine.get_memory_block(
                query=user_input, history=history, domain=domain
            )
        if not raw:
            return ""
        lines = [
            ln for ln in str(raw).splitlines()
            if ln.strip() and not ln.strip().startswith("###")
        ][:max_results]
        if not lines:
            return ""
        return "### CONTACT CONTEXT (brief)\n" + "\n".join(lines)
    except Exception as e:
        logger.debug(f"Lean contact memory unavailable: {e}")
        return ""
