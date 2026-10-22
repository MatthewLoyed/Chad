"""
Register Telegram Webhook

Registers the webhook URL with Telegram Bot API so the bot receives updates.

Usage:
    python scripts/setup_telegram_webhook.py \\
        --url https://your-app.onrender.com/webhooks/telegram \\
        --secret your_webhook_secret
"""

import asyncio
import httpx
import argparse
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.shared.config_manager import ConfigManager


async def setup_webhook(webhook_url: str, secret_token: str = None):
    """
    Register webhook with Telegram Bot API.

    Args:
        webhook_url: Full webhook URL (e.g., https://your-server.com/webhooks/telegram)
        secret_token: Optional webhook secret token for signature verification.
            When omitted, uses telegram.webhook_secret_token / webhook_secrets.telegram
            from secrets.yaml if set (and not a placeholder).
    """
    # Get bot token from config
    try:
        bot_token = ConfigManager.get_secret("telegram.bot_token")
    except Exception:
        bot_token = None

    if not bot_token:
        # Try environment variable
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not bot_token:
        print("ERROR: TELEGRAM_BOT_TOKEN not configured")
        print("   Set it in config/secrets.yaml or TELEGRAM_BOT_TOKEN environment variable")
        return False

    if not secret_token:
        secret_token = (
            ConfigManager.get_secret("telegram.webhook_secret_token")
            or ConfigManager.get_secret("webhook_secrets.telegram")
            or os.getenv("TELEGRAM_WEBHOOK_SECRET")
        )
        if isinstance(secret_token, str) and secret_token.startswith("your-"):
            secret_token = None

    api_url = f"https://api.telegram.org/bot{bot_token}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # 1. Get bot info to verify token
            print("SEARCHING: Verifying bot token...")
            me_response = await client.get(f"{api_url}/getMe")
            me_response.raise_for_status()
            me_data = me_response.json()

            if not me_data.get("ok"):
                print(f"ERROR: Bot token verification failed: {me_data.get('description')}")
                return False

            bot_info = me_data["result"]
            bot_username = bot_info.get("username")
            bot_id = bot_info.get("id")
            print(f"OK: Bot verified: @{bot_username} (ID: {bot_id})")

            # 2. Set webhook
            print(f"\nWEBHOOK: Registering webhook: {webhook_url}")
            webhook_data = {
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True,
            }

            if secret_token:
                webhook_data["secret_token"] = secret_token
                print("SECRET: Using webhook secret token for verification")
            else:
                print("SECRET: No webhook secret configured (token registration only)")

            webhook_response = await client.post(
                f"{api_url}/setWebhook",
                json=webhook_data
            )
            webhook_response.raise_for_status()
            webhook_result = webhook_response.json()

            if not webhook_result.get("ok"):
                print(f"ERROR: Webhook registration failed: {webhook_result.get('description')}")
                return False

            print("OK: Webhook registered successfully")

            # 3. Get webhook info to verify
            print("\nINFO: Verifying webhook configuration...")
            info_response = await client.get(f"{api_url}/getWebhookInfo")
            info_response.raise_for_status()
            info_data = info_response.json()

            if info_data.get("ok"):
                info = info_data["result"]
                print("\nSUMMARY: Webhook Configuration:")
                print(f"   URL: {info.get('url')}")
                print(f"   Has custom certificate: {info.get('has_custom_certificate', False)}")
                print(f"   Pending update count: {info.get('pending_update_count', 0)}")

                if info.get("last_error_date"):
                    from datetime import datetime
                    error_date = datetime.fromtimestamp(info["last_error_date"])
                    print(f"   WARNING: Last error: {info.get('last_error_message')} at {error_date}")

                if info.get("max_connections"):
                    print(f"   Max connections: {info.get('max_connections')}")

            print(f"\nOK: Setup complete! Bot @{bot_username} is ready to receive updates.")
            print(f"\nCHAT: Test it: Open Telegram and send a message to @{bot_username}")

            return True

        except httpx.HTTPError as e:
            print(f"ERROR: HTTP error: {e}")
            return False
        except Exception as e:
            print(f"ERROR: Unexpected error: {e}")
            return False


async def delete_webhook():
    """Delete the webhook (for cleanup or switching to polling mode)."""
    # Get bot token
    try:
        bot_token = ConfigManager.get_secret("telegram.bot_token")
    except:
        bot_token = None

    if not bot_token:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not bot_token:
        print("❌ Error: TELEGRAM_BOT_TOKEN not configured")
        return False

    api_url = f"https://api.telegram.org/bot{bot_token}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            print("🗑️  Deleting webhook...")
            response = await client.post(f"{api_url}/deleteWebhook")
            response.raise_for_status()
            result = response.json()

            if result.get("ok"):
                print("✅ Webhook deleted successfully")
                return True
            else:
                print(f"❌ Failed to delete webhook: {result.get('description')}")
                return False

        except Exception as e:
            print(f"❌ Error: {e}")
            return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Register Telegram webhook for Chad AI Assistant"
    )
    parser.add_argument(
        "--url",
        help="Webhook URL (e.g., https://your-app.onrender.com/webhooks/telegram)"
    )
    parser.add_argument(
        "--secret",
        help="Optional webhook secret token for signature verification"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the webhook instead of registering it"
    )

    args = parser.parse_args()

    if args.delete:
        success = asyncio.run(delete_webhook())
    else:
        if not args.url:
            parser.error("--url is required (unless using --delete)")

        success = asyncio.run(setup_webhook(args.url, args.secret))

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
