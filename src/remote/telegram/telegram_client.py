"""
Telegram Bot API Client

Lightweight synchronous HTTP client for Telegram Bot API.
Uses httpx.Client (sync) to avoid sniffio/asyncio context issues in
Python 3.14 + uvicorn background tasks (asyncio.create_task).
"""

import httpx
import logging
from typing import Optional, Dict, Any
from src.shared.logger import setup_logger

logger = setup_logger("TelegramClient")


class TelegramClient:
    """
    Synchronous HTTP client for Telegram Bot API.

    All methods are synchronous — callers that need to await them should
    use `await asyncio.to_thread(client.send_message, ...)`.
    """

    def __init__(self, bot_token: str):
        """
        Initialize Telegram client.

        Args:
            bot_token: Telegram Bot API token from @BotFather
        """
        self._bot_token = bot_token
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        # Sync client: safe to create at any time with no event loop required
        self._client = httpx.Client(timeout=30.0)

    def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_to_message_id: Optional[int] = None,
        disable_web_page_preview: bool = True
    ) -> Dict[str, Any]:
        """
        Send text message to chat (sync).

        Args:
            chat_id: Unique identifier for target chat
            text: Text of the message to send (1-4096 characters)
            parse_mode: Message formatting (HTML, Markdown, MarkdownV2)
            reply_to_message_id: Optional message ID to reply to
            disable_web_page_preview: Disable link previews

        Returns:
            Response dict with message details
        """
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview
        }

        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        try:
            response = self._client.post(
                f"{self._base_url}/sendMessage",
                json=payload
            )
            
            # If HTML fails with 400 (Bad Request), fallback to plain text
            if response.status_code == 400 and parse_mode:
                logger.warning(f"Telegram HTML send failed (likely bad tags). Retrying as plain text.")
                payload_plain = payload.copy()
                payload_plain["parse_mode"] = None
                response = self._client.post(
                    f"{self._base_url}/sendMessage",
                    json=payload_plain
                )

            response.raise_for_status()
            result = response.json()

            if not result.get("ok"):
                logger.error(f"Telegram API error: {result.get('description')}")
                raise ValueError(f"Telegram API error: {result.get('description')}")

            logger.info(f"Message sent to chat {chat_id}")
            return result.get("result", {})

        except httpx.HTTPError as e:
            logger.error(f"Failed to send Telegram message: {e}")
            raise

    def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str = "HTML"
    ) -> Dict[str, Any]:
        """
        Edit existing message (sync).

        Args:
            chat_id: Unique identifier for target chat
            message_id: Identifier of message to edit
            text: New text of the message
            parse_mode: Message formatting

        Returns:
            Response dict with updated message details
        """
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }

        try:
            response = self._client.post(
                f"{self._base_url}/editMessageText",
                json=payload
            )

            # If HTML fails with 400, retry as plain text (same as send_message).
            if response.status_code == 400 and parse_mode:
                logger.warning(
                    "Telegram HTML edit failed (likely bad tags). Retrying as plain text."
                )
                payload_plain = payload.copy()
                payload_plain["parse_mode"] = None
                response = self._client.post(
                    f"{self._base_url}/editMessageText",
                    json=payload_plain
                )

            response.raise_for_status()

            result = response.json()

            if not result.get("ok"):
                logger.error(f"Telegram API error: {result.get('description')}")
                raise ValueError(f"Telegram API error: {result.get('description')}")

            logger.debug(f"Message {message_id} edited in chat {chat_id}")
            return result.get("result", {})

        except httpx.HTTPError as e:
            logger.error(f"Failed to edit Telegram message: {e}")
            raise

    def send_chat_action(self, chat_id: int, action: str = "typing") -> bool:
        """
        Send a chat action (e.g. typing indicator). Best-effort; returns False on failure.
        """
        try:
            response = self._client.post(
                f"{self._base_url}/sendChatAction",
                json={"chat_id": chat_id, "action": action},
            )
            response.raise_for_status()
            result = response.json()
            return bool(result.get("ok"))
        except Exception as e:
            logger.debug(f"sendChatAction failed for chat {chat_id}: {e}")
            return False

    def get_me(self) -> Dict[str, Any]:
        """
        Verify bot token and get bot information (sync).

        Returns:
            Bot info dict with id, username, first_name, etc.
        """
        try:
            response = self._client.get(f"{self._base_url}/getMe")
            response.raise_for_status()

            result = response.json()

            if not result.get("ok"):
                logger.error(f"Telegram API error: {result.get('description')}")
                raise ValueError("Failed to verify bot token")

            bot_info = result.get("result", {})
            logger.info(f"Bot verified: @{bot_info.get('username')}")
            return bot_info

        except httpx.HTTPError as e:
            logger.error(f"Failed to verify bot: {e}")
            raise

    def send_photo(
        self,
        chat_id: int,
        photo: str,
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send photo to chat (sync). Supports both URL/file_id or local file path upload.

        Args:
            chat_id: Unique identifier for target chat
            photo: Photo URL, file_id, or local file path
            caption: Optional photo caption
            parse_mode: Caption formatting
            filename: Optional custom filename for local upload

        Returns:
            Response dict with message details
        """
        from pathlib import Path

        path = Path(photo) if isinstance(photo, str) else None
        if path and path.is_file():
            display_name = filename or path.name
            data = {"chat_id": str(chat_id)}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = parse_mode

            with path.open("rb") as handle:
                files = {"photo": (display_name, handle)}
                response = self._client.post(
                    f"{self._base_url}/sendPhoto",
                    data=data,
                    files=files,
                )

            if response.status_code == 400 and parse_mode and caption:
                logger.warning("Telegram HTML photo caption failed; retrying plain.")
                data_plain = {"chat_id": str(chat_id), "caption": caption}
                with path.open("rb") as handle:
                    files = {"photo": (display_name, handle)}
                    response = self._client.post(
                        f"{self._base_url}/sendPhoto",
                        data=data_plain,
                        files=files,
                    )
        else:
            payload = {
                "chat_id": chat_id,
                "photo": photo,
                "parse_mode": parse_mode
            }

            if caption:
                payload["caption"] = caption

            response = self._client.post(
                f"{self._base_url}/sendPhoto",
                json=payload
            )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            logger.error(f"Telegram API error: {result.get('description')}")
            raise ValueError(f"Telegram API error: {result.get('description')}")

        logger.info(f"Photo sent to chat {chat_id}")
        return result.get("result", {})


    def get_file(self, file_id: str) -> Dict[str, Any]:
        """Resolve a Telegram file_id to a file_path (relative to Telegram file CDN)."""
        response = self._client.get(
            f"{self._base_url}/getFile",
            params={"file_id": file_id},
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise ValueError(f"Telegram getFile error: {result.get('description')}")
        return result.get("result") or {}

    def download_file(self, file_id: str) -> bytes:
        """Download file bytes for a Telegram file_id via getFile + file CDN."""
        info = self.get_file(file_id)
        file_path = info.get("file_path")
        if not file_path:
            raise ValueError(f"Telegram getFile missing file_path for {file_id}")
        # File CDN uses /file/bot<token>/<file_path> (not the Bot API JSON endpoint).
        url = f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}"
        response = self._client.get(url)
        response.raise_for_status()
        return response.content

    def send_document(
        self,
        chat_id: int,
        document_path: str,
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a local file as a Telegram document (multipart upload)."""
        from pathlib import Path

        path = Path(document_path)
        if not path.is_file():
            raise FileNotFoundError(f"Document not found: {document_path}")

        display_name = filename or path.name
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = parse_mode

        with path.open("rb") as handle:
            files = {"document": (display_name, handle)}
            response = self._client.post(
                f"{self._base_url}/sendDocument",
                data=data,
                files=files,
            )

        if response.status_code == 400 and parse_mode and caption:
            logger.warning("Telegram HTML document caption failed; retrying plain.")
            data_plain = {"chat_id": str(chat_id), "caption": caption}
            with path.open("rb") as handle:
                files = {"document": (display_name, handle)}
                response = self._client.post(
                    f"{self._base_url}/sendDocument",
                    data=data_plain,
                    files=files,
                )

        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise ValueError(f"Telegram API error: {result.get('description')}")
        logger.info(f"Document sent to chat {chat_id}: {display_name}")
        return result.get("result", {})

    def close(self):
        """Close HTTP client connection."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Singleton instance
_telegram_client: Optional[TelegramClient] = None


def get_telegram_client() -> TelegramClient:
    """
    Get singleton Telegram client instance.

    Returns:
        TelegramClient instance

    Raises:
        ValueError: If bot token not configured
    """
    global _telegram_client

    if _telegram_client is None:
        from src.shared.config_manager import ConfigManager

        bot_token = ConfigManager.get_secret("telegram.bot_token")

        if not bot_token:
            import os
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

        if not bot_token:
            raise ValueError(
                "Telegram bot token not configured. "
                "Set TELEGRAM_BOT_TOKEN in config/secrets.yaml or environment"
            )

        _telegram_client = TelegramClient(bot_token)
        logger.info("Telegram client initialized")

    return _telegram_client


def reset_telegram_client():
    """Reset singleton instance (for testing)."""
    global _telegram_client
    _telegram_client = None
