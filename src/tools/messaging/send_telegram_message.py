"""
Send Telegram Message Tool

Enables autonomous Telegram notifications from cron jobs, heartbeat, and skills.
"""

from typing import Optional, Dict, Any
from src.tools.base_tool import BaseTool
from src.remote.telegram.telegram_client import get_telegram_client
from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository
from src.shared.logger import setup_logger

logger = setup_logger("SendTelegramMessageTool")


class SendTelegramMessageTool(BaseTool):
    """
    Send a message to the user via Telegram.

    Use this tool to send notifications, reminders, alerts, or important
    updates to the user via Telegram when they're not actively chatting.
    """

    name = "send_telegram_message"
    description = (
        "Send a message to the user via Telegram. Use for notifications, "
        "reminders, urgent alerts, or important updates that need immediate attention. "
        "The message will be delivered to the user's Telegram app."
    )
    category = "messaging"
    is_side_effecting = True  # Prevent duplicate sends via idempotency cache

    def get_schema(self) -> dict:
        """Get tool schema for function calling."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": (
                            "The message text to send. Can include basic HTML formatting: "
                            "<b>bold</b>, <i>italic</i>, <code>code</code>, "
                            "<a href='url'>link</a>. Max 4096 characters."
                        )
                    },
                    "chat_id": {
                        "type": "integer",
                        "description": (
                            "Optional: specific Telegram chat ID to send to. "
                            "If not provided, sends to the most recently active chat (default user)."
                        )
                    }
                },
                "required": ["message"]
            }
        }

    async def execute(
        self,
        message: str,
        chat_id: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send message via Telegram.

        Args:
            message: Message text to send (supports HTML formatting)
            chat_id: Optional specific chat ID (defaults to most recent active chat)

        Returns:
            Dict with success status, message_id, and chat_id
        """
        try:
            # Get Telegram client
            telegram_client = get_telegram_client()

        except ValueError as e:
            logger.error(f"Telegram client not available: {e}")
            return {
                "success": False,
                "error": (
                    "Telegram is not configured. Please set TELEGRAM_BOT_TOKEN "
                    "in config/secrets.yaml or environment."
                )
            }

        # Get chat repository
        chat_repo = TelegramChatRepository()

        # Optional Redundancy Protection: Only skip if explicitly configured
        from src.shared.config_manager import ConfigManager
        from src.remote.server import ChadServer
        if (
            ConfigManager.get("telegram.suppress_when_gui_connected", False)
            and ChadServer.instance
            and ChadServer.instance.is_limb_connected()
        ):
            logger.info("Skipping Telegram message because a GUI limb is actively connected and suppression is enabled.")
            return {
                "success": True,
                "message": (
                    "Telegram message skipped because a GUI limb (PC/Laptop) is connected. "
                    "The user will see this notification in their GUI instead."
                )
            }

        # If no chat_id specified, use default (most recent active chat)
        if chat_id is None:
            chat_id = chat_repo.get_default_chat_id()

            if chat_id is None:
                logger.warning("No active Telegram chats found")
                return {
                    "success": False,
                    "error": (
                        "No active Telegram chats found. The user must message "
                        "the bot at least once before you can send notifications."
                    )
                }

            logger.info(f"Using default chat ID: {chat_id}")

        # Validate message length
        if len(message) > 4096:
            logger.warning(f"Message truncated from {len(message)} to 4096 characters")
            message = message[:4093] + "..."

        # Send message
        try:
            import asyncio
            result = await asyncio.to_thread(
                telegram_client.send_message,
                chat_id=chat_id,
                text=message,
                parse_mode="HTML"
            )

            message_id = result.get("message_id")

            logger.info(
                f"Telegram message sent successfully: chat_id={chat_id}, "
                f"message_id={message_id}"
            )

            return {
                "success": True,
                "message_id": message_id,
                "chat_id": chat_id,
                "message": f"Message sent to Telegram chat {chat_id}"
            }

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to send message: {str(e)}",
                "chat_id": chat_id
            }
