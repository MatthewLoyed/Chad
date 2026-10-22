"""
Telegram Integration Module

Provides Telegram Bot API client and response routing.
"""

from src.remote.telegram.telegram_client import (
    TelegramClient,
    get_telegram_client,
    reset_telegram_client
)

__all__ = [
    "TelegramClient",
    "get_telegram_client",
    "reset_telegram_client"
]
