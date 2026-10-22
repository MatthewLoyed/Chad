"""
Webhook Manager - Handle incoming webhooks from external services

Supports:
- Gmail Pub/Sub (push notifications for new emails)
- Telegram Bot (incoming messages)
- GitHub (repo events, PRs, issues)
- Google Calendar (event updates)
- Custom webhooks

Security:
- HMAC signature verification
- Token authentication
- IP whitelisting (optional)
- Replay attack prevention
"""

import hmac
import hashlib
import json
import base64
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime, timedelta
from dataclasses import dataclass

from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager

logger = setup_logger("WebhookManager")


@dataclass
class WebhookEvent:
    """Represents a webhook event."""
    service: str
    event_type: str
    payload: Dict[str, Any]
    headers: Dict[str, str]
    timestamp: datetime
    verified: bool = False
    event_id: Optional[str] = None
    metadata: Dict[str, Any] = None


class WebhookManager:
    """
    Manages webhook handlers and verification.

    Usage:
        manager = WebhookManager()
        manager.register_handler("gmail", handle_gmail_webhook)

        # In Starlette route:
        event = await manager.process_webhook("gmail", payload, headers)
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or ConfigManager()
        self._handlers: Dict[str, Callable] = {}
        self._recent_events: List[str] = []  # For replay attack prevention
        self._max_recent = 1000
        self._last_triggered: Dict[str, str] = {}  # service -> ISO timestamp of last processed event

        # Load webhook secrets from config
        self._secrets = self._load_secrets()

    def _load_secrets(self) -> Dict[str, str]:
        """Load webhook secrets for signature verification."""
        # Telegram secret_token may live under nested telegram.* or flat webhook_secrets.*.
        telegram_secret = (
            self.config.get_secret("telegram.webhook_secret_token", "")
            or self.config.get_secret("webhook_secrets.telegram", "")
            or self.config.get_secret("telegram_webhook_token", "")
            or ""
        )
        if isinstance(telegram_secret, str) and telegram_secret.startswith("your-"):
            telegram_secret = ""
        return {
            "gmail": self.config.get_secret("GMAIL_WEBHOOK_SECRET", "")
            or self.config.get_secret("webhook_secrets.gmail", ""),
            "telegram": self.config.get_secret("TELEGRAM_BOT_TOKEN", "")
            or self.config.get_secret("telegram.bot_token", ""),
            "telegram_webhook_token": telegram_secret,
            "github": self.config.get_secret("GITHUB_WEBHOOK_SECRET", "")
            or self.config.get_secret("webhook_secrets.github", ""),
            "calendar": self.config.get_secret("CALENDAR_WEBHOOK_SECRET", "")
            or self.config.get_secret("webhook_secrets.calendar", ""),
        }

    def register_handler(self, service: str, handler: Callable):
        """
        Register a webhook handler for a service.

        Args:
            service: Service name (e.g., "gmail", "telegram")
            handler: Async function that processes the webhook
        """
        self._handlers[service] = handler
        logger.info(f"Registered webhook handler for: {service}")

    async def process_webhook(
        self,
        service: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        verify: bool = True
    ) -> Optional[WebhookEvent]:
        """
        Process an incoming webhook.

        Args:
            service: Service name
            payload: Webhook payload (JSON)
            headers: HTTP headers
            verify: Whether to verify signature

        Returns:
            WebhookEvent if valid, None if rejected
        """
        logger.info(f"Processing webhook from: {service}")

        # Create event
        event = WebhookEvent(
            service=service,
            event_type=self._detect_event_type(service, payload, headers),
            payload=payload,
            headers=headers,
            timestamp=datetime.now(),
            event_id=self._extract_event_id(service, payload, headers),
            metadata={}
        )

        # Pre-process Gmail Pub/Sub data
        if service == "gmail" and "message" in payload:
            data_b64 = payload["message"].get("data")
            if data_b64:
                try:
                    import base64
                    decoded_str = base64.b64decode(data_b64).decode("utf-8")
                    event.metadata = json.loads(decoded_str)
                    logger.debug(f"Decoded Gmail Pub/Sub data: {event.metadata}")
                except Exception as e:
                    logger.warning(f"Failed to decode Gmail Pub/Sub data: {e}")

        # Verify signature if required
        if verify:
            if not self._verify_signature(event):
                logger.warning(f"Webhook signature verification failed for: {service}")
                return None
            event.verified = True

        # Check for replay attack
        if self._is_replay_attack(event):
            logger.info(f"Replay detected for: {service}. Acknowledging without re-processing.")
            return "REPLAY"

        # Get handler
        handler = self._handlers.get(service)
        if not handler:
            logger.warning(f"No handler registered for: {service}")
            return None

        # Execute handler
        try:
            await handler(event)
            logger.info(f"Successfully processed webhook from: {service}")
            self._last_triggered[service] = event.timestamp.isoformat()
            return event
        except Exception as e:
            logger.error(f"Webhook handler failed for {service}: {e}", exc_info=True)
            return None

    def get_status(self) -> Dict[str, Dict[str, Any]]:
        """Per-service configured/last-triggered status, for the Autonomous Actions settings panel."""
        return {
            service: {
                "configured": bool(self._secrets.get(service)),
                "last_triggered_at": self._last_triggered.get(service),
            }
            for service in ("gmail", "telegram", "github", "calendar")
        }

    def _verify_signature(self, event: WebhookEvent) -> bool:
        """Verify webhook signature based on service."""
        service = event.service

        if service == "gmail":
            return self._verify_gmail_signature(event)
        elif service == "telegram":
            return self._verify_telegram_signature(event)
        elif service == "github":
            return self._verify_github_signature(event)
        else:
            # No verification for unknown services
            logger.warning(f"No signature verification for: {service}")
            return True

    def _verify_gmail_signature(self, event: WebhookEvent) -> bool:
        """
        Verify Gmail Pub/Sub push notification.
        Uses a simple token match for security.
        """
        expected_token = self.config.get_secret("GMAIL_WEBHOOK_SECRET", "")
        
        # If no secret is configured, skip verification (not recommended for production)
        if not expected_token:
            return True
            
        # Check for token in query parameters (passed from Pub/Sub URL)
        # Headers are usually lowercase in Starlette/FastAPI
        token = event.headers.get("x-chad-token")
        
        if not token:
            logger.warning("[ GMAIL ] Webhook rejected: Missing security token")
            return False

        if token != expected_token:
            logger.error(f"[ GMAIL ] Webhook rejected: Token mismatch.")
            logger.error(f"          Expected ends with: ...{expected_token[-4:] if expected_token else 'NONE'}")
            logger.error(f"          Received ends with: ...{token[-4:]}")
            return False

        return True

    def _verify_telegram_signature(self, event: WebhookEvent) -> bool:
        """
        Verify Telegram webhook.

        Telegram only delivers to the URL registered with your bot token. When a
        secret_token was set at registration time, Telegram also sends
        X-Telegram-Bot-Api-Secret-Token — require an exact match if configured.
        """
        expected_token = (self._secrets.get("telegram_webhook_token") or "").strip()
        if not expected_token:
            # No secret configured — token registration alone authenticates delivery.
            return True

        headers = {str(k).lower(): v for k, v in (event.headers or {}).items()}
        secret_token = (headers.get("x-telegram-bot-api-secret-token") or "").strip()
        if secret_token != expected_token:
            logger.warning("[ TELEGRAM ] Webhook rejected: secret_token mismatch or missing")
            return False
        return True

    def _verify_github_signature(self, event: WebhookEvent) -> bool:
        """
        Verify GitHub webhook using HMAC SHA-256.

        GitHub sends X-Hub-Signature-256: sha256=<hmac>
        """
        signature = event.headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            return False

        expected_sig = signature[7:]  # Remove "sha256=" prefix
        secret = self._secrets.get("github", "").encode()

        if not secret:
            logger.warning("No GitHub webhook secret configured")
            return False

        # Compute HMAC
        payload_bytes = json.dumps(event.payload).encode()
        computed = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()

        return hmac.compare_digest(computed, expected_sig)

    def _is_replay_attack(self, event: WebhookEvent) -> bool:
        """Check if this event was already processed (replay attack)."""
        # Create unique event ID signature
        # Priority 1: Service-provided unique ID (e.g., update_id)
        # Priority 2: Hash of payload + service
        
        signature = event.event_id
        if not signature:
            # Fallback to payload hash if no unique ID found
            payload_json = json.dumps(event.payload, sort_keys=True)
            signature = f"{event.service}:{hashlib.md5(payload_json.encode()).hexdigest()}"
        else:
            signature = f"{event.service}:{signature}"

        if signature in self._recent_events:
            logger.info(f"[ WEBHOOK ] : Replay detected for {signature}. Skipping.")
            return True

        # Add to recent events
        self._recent_events.append(signature)

        # Trim old events
        if len(self._recent_events) > self._max_recent:
            self._recent_events = self._recent_events[-self._max_recent:]

        return False

    def _extract_event_id(self, service: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Optional[str]:
        """Extract a unique event ID from the service-specific payload/headers."""
        try:
            if service == "telegram":
                return str(payload.get("update_id", ""))
            
            elif service == "gmail":
                message = payload.get("message", {})
                return message.get("messageId")
            
            elif service == "github":
                return headers.get("X-GitHub-Delivery") or headers.get("x-github-delivery")
            
            elif service == "calendar":
                return payload.get("id")
        except Exception as e:
            logger.warning(f"Failed to extract event ID for {service}: {e}")
            
        return None

    def _detect_event_type(
        self,
        service: str,
        payload: Dict[str, Any],
        headers: Dict[str, str]
    ) -> str:
        """Detect the specific event type from payload."""
        if service == "gmail":
            return "new_email"  # Gmail Pub/Sub only sends new email notifications

        elif service == "telegram":
            if "message" in payload:
                return "message"
            elif "callback_query" in payload:
                return "callback"
            elif "inline_query" in payload:
                return "inline_query"
            return "unknown"

        elif service == "github":
            return headers.get("x-github-event", "unknown")

        elif service == "calendar":
            return payload.get("kind", "unknown")

        return "unknown"


# Singleton instance
_webhook_manager: Optional[WebhookManager] = None


def get_webhook_manager() -> WebhookManager:
    """Get singleton webhook manager."""
    global _webhook_manager
    if _webhook_manager is None:
        _webhook_manager = WebhookManager()
    return _webhook_manager
