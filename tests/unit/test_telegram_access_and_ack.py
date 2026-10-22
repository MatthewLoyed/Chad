"""Unit tests for Telegram allowlist + webhook secret verification + ack map."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.remote.telegram import access as tg_access
from src.remote.telegram.telegram_response_router import TelegramResponseRouter
from src.remote.webhooks.webhook_manager import WebhookEvent, WebhookManager


@pytest.fixture
def fresh_config(monkeypatch):
    """Isolate ConfigManager.get_secret for allowlist tests."""
    store = {}

    def fake_get_secret(key, default=None):
        return store.get(key, default)

    monkeypatch.setattr(tg_access.ConfigManager, "get_secret", staticmethod(fake_get_secret))
    return store


def test_allowlist_fail_closed_when_empty(fresh_config):
    fresh_config["telegram.allowed_user_ids"] = []
    fresh_config["telegram.allowed_chat_ids"] = []
    assert tg_access.is_authorized(1, 1) is False
    assert tg_access.authorize_inbound(1, 1) is False


def test_allowlist_matches_user_id(fresh_config):
    fresh_config["telegram.allowed_user_ids"] = [42]
    fresh_config["telegram.allowed_chat_ids"] = []
    assert tg_access.is_authorized(42, 99) is True
    assert tg_access.is_authorized(41, 99) is False


def test_allowlist_matches_chat_id(fresh_config):
    fresh_config["telegram.allowed_user_ids"] = []
    fresh_config["telegram.allowed_chat_ids"] = [77]
    assert tg_access.is_authorized(1, 77) is True
    assert tg_access.is_authorized(1, 78) is False


def test_pair_on_first_message_seeds_allowlist(fresh_config, monkeypatch):
    fresh_config["telegram.allowed_user_ids"] = []
    fresh_config["telegram.allowed_chat_ids"] = []
    fresh_config["telegram.pair_on_first_message"] = True

    updates = {}

    class FakeRepo:
        def update_secret(self, key, value):
            updates[key] = value
            fresh_config[key] = value
            return True

    monkeypatch.setattr(
        "src.infrastructure.repositories.config_repository.ConfigRepository",
        FakeRepo,
    )
    monkeypatch.setattr(tg_access.ConfigManager, "_loaded", False, raising=False)
    monkeypatch.setattr(
        tg_access.ConfigManager, "load_config", staticmethod(lambda: None)
    )

    assert tg_access.authorize_inbound(8626369319, 8626369319) is True
    assert updates["telegram.allowed_user_ids"] == [8626369319]
    assert updates["telegram.allowed_chat_ids"] == [8626369319]
    assert updates["telegram.pair_on_first_message"] is False


def test_telegram_webhook_secret_required_when_configured(monkeypatch):
    mgr = WebhookManager.__new__(WebhookManager)
    mgr._secrets = {"telegram_webhook_token": "s3cret"}
    mgr.config = MagicMock()

    bad = WebhookEvent(
        service="telegram",
        event_type="message",
        payload={},
        headers={},
        timestamp=__import__("datetime").datetime.now(),
    )
    assert mgr._verify_telegram_signature(bad) is False

    good = WebhookEvent(
        service="telegram",
        event_type="message",
        payload={},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        timestamp=__import__("datetime").datetime.now(),
    )
    assert mgr._verify_telegram_signature(good) is True


def test_telegram_webhook_secret_optional_when_unset():
    mgr = WebhookManager.__new__(WebhookManager)
    mgr._secrets = {"telegram_webhook_token": ""}
    event = WebhookEvent(
        service="telegram",
        event_type="message",
        payload={},
        headers={},
        timestamp=__import__("datetime").datetime.now(),
    )
    assert mgr._verify_telegram_signature(event) is True


def test_webhook_manager_loads_nested_telegram_secret(monkeypatch):
    secrets = {
        "telegram.webhook_secret_token": "nested-secret",
        "telegram.bot_token": "1:ABC",
    }

    class FakeConfig:
        def get_secret(self, key, default=None):
            return secrets.get(key, default)

    mgr = WebhookManager(config=FakeConfig())
    assert mgr._secrets["telegram_webhook_token"] == "nested-secret"


def test_webhook_manager_ignores_placeholder_telegram_secret(monkeypatch):
    secrets = {
        "telegram.webhook_secret_token": "your-random-secret-string-here",
        "webhook_secrets.telegram": "your-telegram-webhook-secret",
    }

    class FakeConfig:
        def get_secret(self, key, default=None):
            return secrets.get(key, default)

    mgr = WebhookManager(config=FakeConfig())
    assert mgr._secrets["telegram_webhook_token"] == ""


@pytest.mark.asyncio
async def test_router_sends_on_turn_completed(monkeypatch):
    bus = MagicMock()
    router = TelegramResponseRouter(bus)

    client = MagicMock()
    client.send_message.return_value = {"message_id": 11}
    router._telegram_client = client

    session_key = "main:8626369319:telegram:8626369319"
    from src.core.orchestration.conversation_orchestrator import TurnInitiator

    turn_context = SimpleNamespace(
        platform="telegram",
        session_key=session_key,
        model_response="Hello back",
        initiator=TurnInitiator.USER,
    )

    event = SimpleNamespace(turn_id="t1", turn_context=turn_context)
    await router._handle_turn_completed(event)

    client.send_message.assert_called_once()
    _args, kwargs = client.send_message.call_args
    assert kwargs.get("text") == "hello back" or (
        _args and len(_args) >= 2 and _args[1] == "hello back"
    )
    client.edit_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_front_desk_ack_does_not_register_for_edit(monkeypatch):
    bus = MagicMock()
    router = TelegramResponseRouter(bus)
    client = MagicMock()
    client.send_message.return_value = {"message_id": 55}
    router._telegram_client = client

    session_key = "main:8626369319:telegram:8626369319"
    mid = await router.send_front_desk_ack(session_key, "On it Matthew — checking email")
    assert mid == 55
    client.send_message.assert_called_once()
