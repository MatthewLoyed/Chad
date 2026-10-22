"""
Telegram Response Router

Event-driven response routing from orchestrator back to Telegram chats.
Listens for TurnCompleted events and sends responses via Telegram Bot API.

Always sendMessage — never editMessageText. Contact front desk may send a
personal ack first; the agent answer (if any) is a separate new message.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, List, Optional, Tuple

from src.shared.logger import setup_logger
from src.application.events.event_bus import EventBus
from src.application.events.events import TurnCompleted
from src.core.intelligence.session_key_manager import SessionKeyManager
from src.remote.telegram.telegram_client import get_telegram_client
from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository

logger = setup_logger("TelegramResponseRouter")

_active_router: Optional["TelegramResponseRouter"] = None


def get_active_telegram_router() -> Optional["TelegramResponseRouter"]:
    return _active_router


def _is_silent_heartbeat_response(text: Optional[str]) -> bool:
    """Return True if the autonomous response indicates silence or nothing new to report."""
    if not text:
        return True
    cleaned = text.strip()
    upper = cleaned.upper()
    if upper == "HEARTBEAT_OK" or upper.startswith("HEARTBEAT_OK"):
        return True

    # If the response explicitly states intention to stay quiet or that nothing is urgent
    lower = cleaned.lower()
    suppression_phrases = (
        "stay quiet",
        "staying quiet",
        "i'll stay quiet",
        "i will stay quiet",
        "nothing urgent",
        "nothing new",
        "nothing to report",
        "no urgent emails",
        "no urgent email",
        "no new emails",
        "no new email",
        "inbox is clear",
        "heartbeat scan",
        "quick heartbeat",
        "all quiet",
        "all clear",
        "came back empty",
        "history id",
        "history event",
        "spam bot",
        "no fresh email",
        "nothing worth flagging",
        "routine notification",
        "routine receipt",
        "nothing actionable",
        "stay silent",
        "stay_silent",
    )
    if any(p in lower for p in suppression_phrases):
        return True
    return False


def format_telegram_prose_lowercase(text: str) -> str:
    """Lowercase all prose text while preserving URLs, HTML tags, and code blocks.

    Ensures 100% adherence to lowercase texting style even if the model slips up.
    """
    if not text:
        return text

    # Pattern matches:
    # 1. Code blocks/spans: <code>.*?</code> or <pre>.*?</pre>
    # 2. General HTML tags: <[^>]+>
    # 3. URLs: https?://[^\s<]+
    pattern = re.compile(
        r"(<code>.*?</code>|<pre>.*?</pre>|<[^>]+>|https?://[^\s<]+)",
        flags=re.DOTALL | re.IGNORECASE,
    )
    parts = pattern.split(text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(part.lower())
        else:
            out.append(part)
    return "".join(out)


def _clean_conversational_prefix(text: str) -> str:
    """Strip repetitive bot greetings like 'Hey Matthew,' or 'Hey Matthew!'."""
    cleaned = re.sub(
        r"^(?:(?:hey|hi|hello)\s+(?:matthew|there)[,!:.]*\s*|matthew[,!:]\s*)+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    return cleaned if cleaned else text


class TelegramResponseRouter:
    """
    Routes orchestrator responses back to Telegram chats.

    Subscribes to TurnCompleted events and sends responses to
    originating Telegram chats based on session key.
    """

    def __init__(self, event_bus: EventBus):
        global _active_router
        self._event_bus = event_bus
        self._telegram_client = None  # Lazy initialization
        self._chat_repo = TelegramChatRepository()
        self._recent_autonomous_alerts: Dict[int, List[Tuple[str, float]]] = {}

        self._event_bus.subscribe(TurnCompleted, self._handle_turn_completed)
        _active_router = self

        logger.info("Telegram response router initialized")

    def _get_telegram_client(self):
        """Lazy initialization of Telegram client."""
        if self._telegram_client is None:
            try:
                self._telegram_client = get_telegram_client()
            except ValueError as e:
                logger.warning(f"Telegram client not available: {e}")
                return None
        return self._telegram_client

    def _chat_id_from_session(self, session_key: str) -> Optional[int]:
        try:
            return int(SessionKeyManager.parse_key(session_key).peer)
        except Exception:
            resolved = self._chat_repo.get_chat_id(session_key)
            return int(resolved) if resolved is not None else None

    async def send_typing(self, chat_id: int) -> None:
        telegram_client = self._get_telegram_client()
        if not telegram_client:
            return
        try:
            await asyncio.to_thread(telegram_client.send_chat_action, chat_id, "typing")
        except Exception as e:
            logger.debug(f"[ ROUTER ] typing failed for chat {chat_id}: {e}")

    async def send_typing_for_session(self, session_key: str) -> None:
        chat_id = self._chat_id_from_session(session_key)
        if chat_id is not None:
            await self.send_typing(chat_id)

    async def send_front_desk_ack(self, session_key: str, text: str) -> Optional[int]:
        """Send the 9B contact reply as a normal new message (not edited later)."""
        chat_id = self._chat_id_from_session(session_key)
        if chat_id is None:
            logger.warning(f"[ ROUTER ] No chat_id for session {session_key}")
            return None
        mid = await self.send_text(chat_id, (text or "").strip() or "On it — working on that.")
        if mid is not None:
            logger.info(
                f"[ ROUTER ] Front-desk ack sent to chat {chat_id} (message_id={mid})"
            )
        return mid

    async def send_text(self, chat_id: int, text: str) -> Optional[int]:
        """Send a plain Telegram message."""
        telegram_client = self._get_telegram_client()
        if not telegram_client:
            return None
        body = (text or "").strip()
        if not body:
            return None
        body = format_telegram_prose_lowercase(body)
        if len(body) > 4096:
            body = body[:4093] + "..."
        try:
            result = await asyncio.to_thread(
                telegram_client.send_message,
                chat_id=chat_id,
                text=body,
                parse_mode="HTML",
            )
            mid = result.get("message_id") if isinstance(result, dict) else None
            return int(mid) if mid is not None else None
        except Exception as e:
            logger.warning(f"[ ROUTER ] send_text failed for chat {chat_id}: {e}")
            return None

    async def notify_stopped(self, session_key: str, chat_id: int) -> None:
        """Acknowledge /stop with a new message."""
        await self.send_text(chat_id, "Stopped.")

    async def send_unauthorized_notice(self, chat_id: int, text: str) -> None:
        """Best-effort rejection notice to an unauthorized sender."""
        await self.send_text(chat_id, text)

    def _is_duplicate_autonomous_alert(self, chat_id: int, text: str, ttl_seconds: float = 14400.0) -> bool:
        """Check if an autonomous alert is a duplicate of one sent in the last 4 hours."""
        now = time.time()
        history = [
            (msg, ts)
            for msg, ts in self._recent_autonomous_alerts.get(chat_id, [])
            if now - ts < ttl_seconds
        ]
        self._recent_autonomous_alerts[chat_id] = history

        norm = re.sub(r"\W+", " ", text.lower()).strip()
        if not norm:
            return False

        for past_msg, _ in history:
            past_norm = re.sub(r"\W+", " ", past_msg.lower()).strip()
            if norm == past_norm or (len(norm) > 20 and norm in past_norm) or (len(past_norm) > 20 and past_norm in norm):
                return True

        history.append((text, now))
        self._recent_autonomous_alerts[chat_id] = history
        return False

    async def _handle_turn_completed(self, event: TurnCompleted):
        """
        Handle TurnCompleted event and route response to Telegram.

        Args:
            event: TurnCompleted event from orchestrator
        """
        turn_context = event.turn_context

        is_telegram = (
            (turn_context.platform == "telegram") or
            (turn_context.session_key and ":telegram:" in turn_context.session_key)
        )

        if not is_telegram:
            logger.debug(
                f"[ ROUTER ] Ignoring non-Telegram turn {event.turn_id} "
                f"(platform: {turn_context.platform}, session: {turn_context.session_key})"
            )
            return

        if not turn_context.session_key:
            logger.warning(
                f"[ ROUTER ] Telegram turn {event.turn_id} has no session key. Cannot route."
            )
            return

        try:
            parsed = SessionKeyManager.parse_key(turn_context.session_key)
            chat_id = int(parsed.peer)
            response_text = turn_context.model_response

            tools_executed = getattr(turn_context, "tools_executed", []) or []
            if "stay_silent" in tools_executed:
                logger.info(
                    f"[ ROUTER ] Suppressed turn {event.turn_id} in chat {chat_id} because `stay_silent` tool was executed."
                )
                return

            from src.core.orchestration.conversation_orchestrator import TurnInitiator
            is_autonomous = turn_context.initiator in (
                TurnInitiator.SYSTEM,
                TurnInitiator.WEBHOOK,
                TurnInitiator.SKILL,
            )

            if is_autonomous:
                if _is_silent_heartbeat_response(response_text):
                    logger.info(
                        f"[ ROUTER ] Suppressed silent/non-urgent autonomous response for turn {event.turn_id} "
                        f"in chat {chat_id}: {response_text[:60] if response_text else 'None'}"
                    )
                    return

                from src.shared.config_manager import ConfigManager
                from src.remote.server import ChadServer
                if (
                    ConfigManager.get("telegram.suppress_when_gui_connected", False)
                    and ChadServer.instance
                    and ChadServer.instance.is_limb_connected()
                ):
                    logger.info(
                        f"[ ROUTER ] Suppressing autonomous {turn_context.initiator.value} "
                        f"response to Telegram because a GUI limb is connected."
                    )
                    return

                if response_text and self._is_duplicate_autonomous_alert(chat_id, response_text):
                    logger.info(
                        f"[ ROUTER ] Suppressed duplicate autonomous alert for turn {event.turn_id} "
                        f"in chat {chat_id}"
                    )
                    return
            elif response_text and response_text.strip().upper() == "HEARTBEAT_OK":
                logger.info(
                    f"[ ROUTER ] Suppressed silent HEARTBEAT_OK for turn {event.turn_id} "
                    f"in chat {chat_id}"
                )
                return

            if not response_text:
                logger.warning(
                    f"No response text for turn in chat {chat_id}. Sending fallback."
                )
                response_text = (
                    "i processed your request but didn't generate a text response. "
                    "please try again or check my status."
                )

            # Clean any repetitive bot greeting formulas like 'Hey Matthew!'
            response_text = _clean_conversational_prefix(response_text)
            response_text = format_telegram_prose_lowercase(response_text)

            if len(response_text) > 4096:
                logger.warning(
                    f"Response truncated from {len(response_text)} to 4096 chars"
                )
                response_text = response_text[:4093] + "..."

            telegram_client = self._get_telegram_client()
            if not telegram_client:
                logger.error("Telegram client not available, cannot send response")
                return

            await asyncio.to_thread(
                telegram_client.send_message,
                chat_id=chat_id,
                text=response_text,
                parse_mode="HTML",
            )
            logger.info(
                f"Sent response to Telegram chat {chat_id} "
                f"(session: {turn_context.session_key})"
            )

            # Deliver any artifacts produced during this Telegram turn as photos or documents.
            try:
                from pathlib import Path
                from src.remote.telegram.artifact_delivery import take_pending_artifacts

                pending = take_pending_artifacts(turn_context.session_key)
                for art in pending:
                    path = art.get("path") or ""
                    filename = art.get("filename") or "file"
                    if not path:
                        continue

                    ext = Path(path).suffix.lower()
                    is_photo = ext in {".png", ".jpg", ".jpeg", ".webp"}
                    sent = False

                    if is_photo and hasattr(telegram_client, "send_photo"):
                        try:
                            await asyncio.to_thread(
                                telegram_client.send_photo,
                                chat_id,
                                path,
                                caption=filename,
                            )
                            sent = True
                            logger.info(
                                f"Sent photo to Telegram chat {chat_id}: {filename}"
                            )
                        except Exception as photo_err:
                            logger.warning(
                                f"[ ROUTER ] send_photo failed for {filename}, falling back to document: {photo_err}"
                            )

                    if not sent:
                        try:
                            await asyncio.to_thread(
                                telegram_client.send_document,
                                chat_id,
                                path,
                                caption=filename,
                                filename=filename,
                            )
                            logger.info(
                                f"Sent document to Telegram chat {chat_id}: {filename}"
                            )
                        except Exception as doc_err:
                            logger.warning(
                                f"[ ROUTER ] send_document failed for {filename}: {doc_err}"
                            )
            except Exception as art_err:
                logger.debug(f"[ ROUTER ] artifact delivery skipped: {art_err}")

        except ValueError as e:
            logger.error(f"Failed to parse session key: {e}")
        except Exception as e:
            logger.error(f"Failed to send Telegram response: {e}", exc_info=True)

    def get_stats(self):
        return {
            "chat_stats": self._chat_repo.get_stats(),
            "telegram_client_available": self._get_telegram_client() is not None,
        }
