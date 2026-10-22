"""Register / refresh the Telegram Bot API webhook URL.

Used by the CLI setup script and by Brain boot when ``telegram.public_base_url``
(or ``TELEGRAM_PUBLIC_BASE_URL``) is set — so launching a Brain that owns that
tunnel re-aims Telegram at this process.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("TelegramWebhookRegister")

_PLACEHOLDER_PREFIXES = ("your-", "paste-", "changeme")


def resolve_public_webhook_url() -> Optional[str]:
    """Full ``…/webhooks/telegram`` URL, or None if auto-register is not configured."""
    base = (
        ConfigManager.get_secret("telegram.public_base_url")
        or ConfigManager.get_secret("telegram.webhook_base_url")
        or os.getenv("TELEGRAM_PUBLIC_BASE_URL")
        or ""
    )
    base = str(base).strip().rstrip("/")
    if not base or base.lower().startswith(_PLACEHOLDER_PREFIXES):
        return None
    if base.endswith("/webhooks/telegram"):
        return base
    return f"{base}/webhooks/telegram"


def _bot_token() -> Optional[str]:
    token = ConfigManager.get_secret("telegram.bot_token") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or str(token).startswith("123456789"):
        return None
    return str(token).strip()


def _secret_token() -> Optional[str]:
    secret = (
        ConfigManager.get_secret("telegram.webhook_secret_token")
        or ConfigManager.get_secret("webhook_secrets.telegram")
        or os.getenv("TELEGRAM_WEBHOOK_SECRET")
    )
    if isinstance(secret, str) and (
        not secret.strip() or secret.strip().lower().startswith(_PLACEHOLDER_PREFIXES)
    ):
        return None
    return str(secret).strip() if secret else None


async def register_telegram_webhook(
    webhook_url: Optional[str] = None,
    *,
    drop_pending_updates: bool = True,
) -> Tuple[bool, Dict[str, Any]]:
    """Call Telegram ``setWebhook``. Returns ``(ok, detail)``."""
    url = (webhook_url or resolve_public_webhook_url() or "").strip()
    if not url:
        return False, {"error": "no_public_base_url"}

    token = _bot_token()
    if not token:
        return False, {"error": "no_bot_token"}

    secret = _secret_token()
    api = f"https://api.telegram.org/bot{token}"
    payload: Dict[str, Any] = {
        "url": url,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": drop_pending_updates,
    }
    if secret:
        payload["secret_token"] = secret

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            me = await client.get(f"{api}/getMe")
            me.raise_for_status()
            me_json = me.json()
            if not me_json.get("ok"):
                return False, {"error": "getMe_failed", "detail": me_json}

            resp = await client.post(f"{api}/setWebhook", json=payload)
            resp.raise_for_status()
            body = resp.json()
            if not body.get("ok"):
                return False, {"error": "setWebhook_failed", "detail": body}

            info = await client.get(f"{api}/getWebhookInfo")
            info.raise_for_status()
            info_json = info.json().get("result") or {}
            return True, {
                "url": info_json.get("url") or url,
                "username": (me_json.get("result") or {}).get("username"),
                "pending_update_count": info_json.get("pending_update_count"),
                "last_error_message": info_json.get("last_error_message"),
            }
    except Exception as e:
        logger.warning(f"[ TELEGRAM ] setWebhook failed: {e}")
        return False, {"error": str(e)}


async def maybe_auto_register_telegram_webhook() -> None:
    """Best-effort boot hook: re-aim Telegram at this Brain when a public URL is set."""
    url = resolve_public_webhook_url()
    if not url:
        logger.debug("[ TELEGRAM ] Auto-register skipped (no telegram.public_base_url).")
        return
    ok, detail = await register_telegram_webhook(url)
    if ok:
        logger.warning(
            f"[ TELEGRAM ] Webhook auto-registered → {detail.get('url')} "
            f"(@{detail.get('username')})"
        )
        if detail.get("last_error_message"):
            logger.warning(
                f"[ TELEGRAM ] Webhook last_error still set: {detail.get('last_error_message')}"
            )
    else:
        logger.warning(f"[ TELEGRAM ] Webhook auto-register failed: {detail}")
