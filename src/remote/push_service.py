"""
Web Push notification service for the Brain server.

Maintains VAPID key pair (auto-generated, stored in data/push_vapid.json),
stores push subscriptions in memory, and sends Web Push messages to all
registered subscribers using pywebpush.

Called from LimbCoordinator when routing AUTONOMOUS_NOTIFICATION or
TOOL_APPROVAL_REQUEST messages to connected limbs.
"""
import json
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("PushService")

_VAPID_PATH = Path("data/push_vapid.json")
_CONTACT = "mailto:chad@local"  # Arbitrary — required by VAPID spec

_service: "PushService | None" = None


def get_push_service() -> "PushService":
    global _service
    if _service is None:
        _service = PushService()
    return _service


class PushService:
    def __init__(self) -> None:
        self._subscriptions: list[dict] = []
        self._vapid_private_key: str | None = None
        self._vapid_public_key: str | None = None
        self._ready = False
        self._load_or_generate_vapid()

    # ── VAPID key management ──────────────────────────────────────────────────

    def _load_or_generate_vapid(self) -> None:
        try:
            from py_vapid import Vapid  # type: ignore[import]
        except ImportError:
            logger.warning("[Push] pywebpush/py_vapid not installed — push disabled. Run: pip install pywebpush")
            return

        if _VAPID_PATH.exists():
            try:
                data = json.loads(_VAPID_PATH.read_text())
                self._vapid_private_key = data["private_key"]
                self._vapid_public_key = data["public_key"]
                self._ready = True
                logger.info("[Push] VAPID keys loaded from %s", _VAPID_PATH)
                return
            except Exception as exc:
                logger.warning("[Push] Failed to load VAPID keys: %s — regenerating", exc)

        # Generate a fresh key pair using py_vapid helpers
        vapid = Vapid()
        vapid.generate_keys()
        priv = vapid.private_pem().decode()
        # Application-server key = uncompressed EC point, base64url (no padding).
        # cryptography>=42 removed the monkey-patched EllipticCurvePublicKey.serialize()
        # that older py_vapid relied on (the rust-backed ECPublicKey has no such method),
        # so derive the raw point ourselves via public_bytes() for forward compatibility.
        import base64
        from cryptography.hazmat.primitives import serialization
        raw_point = vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        pub = base64.urlsafe_b64encode(raw_point).rstrip(b"=").decode()

        _VAPID_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"private_key": priv, "public_key": pub}
        _VAPID_PATH.write_text(json.dumps(data, indent=2))
        self._vapid_private_key = priv
        self._vapid_public_key = pub
        self._ready = True
        logger.info("[Push] Generated new VAPID keys → %s", _VAPID_PATH)

    @property
    def public_key(self) -> str | None:
        return self._vapid_public_key

    # ── Subscription management ───────────────────────────────────────────────

    def add_subscription(self, sub: dict) -> None:
        endpoint = sub.get("endpoint")
        if not endpoint:
            return
        self._subscriptions = [s for s in self._subscriptions if s.get("endpoint") != endpoint]
        self._subscriptions.append(sub)
        logger.info("[Push] Subscription registered (total=%d)", len(self._subscriptions))

    def remove_subscription(self, endpoint: str) -> None:
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.get("endpoint") != endpoint]
        if len(self._subscriptions) < before:
            logger.info("[Push] Subscription removed (total=%d)", len(self._subscriptions))

    # ── Sending ───────────────────────────────────────────────────────────────

    async def send(self, title: str, body: str, tag: str = "chad", require_interaction: bool = False) -> None:
        if not self._ready or not self._subscriptions:
            return
        payload = json.dumps({
            "title": title,
            "body": body,
            "tag": tag,
            "requireInteraction": require_interaction,
        })
        await asyncio.to_thread(self._send_sync, payload)

    def _send_sync(self, payload: str) -> None:
        try:
            from pywebpush import webpush, WebPushException  # type: ignore[import]
        except ImportError:
            logger.warning("[Push] pywebpush not available — skipping push")
            return

        dead: list[str] = []
        for sub in list(self._subscriptions):
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=self._vapid_private_key,
                    vapid_claims={"sub": _CONTACT},
                )
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    dead.append(sub.get("endpoint", ""))
                else:
                    logger.warning("[Push] WebPushException for %s: %s", sub.get("endpoint", "?")[:40], exc)
            except Exception as exc:
                logger.warning("[Push] Failed to push to %s: %s", sub.get("endpoint", "?")[:40], exc)

        for endpoint in dead:
            self.remove_subscription(endpoint)
