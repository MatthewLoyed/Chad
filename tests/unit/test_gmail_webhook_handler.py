# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
"""GmailWebhookHandler — PubSub notification -> sequential worker -> orchestrator dispatch.

issue #216 flagged this as a total gap: src/remote/webhooks/gmail_handler.py had zero test
references anywhere in tests/. These are unit tests against fakes for
GoogleWorkspaceManager/TelegramChatRepository/the orchestrator, driving the real
handle_webhook()/_worker_loop() so the idempotency check, re-queue-when-not-ready, and
error-doesn't-stall-the-queue behaviors are actually proven rather than assumed.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.remote.webhooks.gmail_handler import GmailWebhookHandler


@pytest.fixture(autouse=True)
def enable_webhooks_for_worker_tests(monkeypatch):
    # These tests exercise the enabled worker. Master-switch blocking has its
    # own coverage in test_autonomous_master_switch.py.
    monkeypatch.setattr("src.core.monitoring.daemon_status.daemon_enabled", lambda name: True)


class _FakeWorkspaceManager:
    def __init__(self, last_history_id=None):
        self._last_history_id = last_history_id

    def get_last_history_id(self):
        return self._last_history_id


class _FakeChatRepo:
    def __init__(self, default_chat_id=None, session_key="tg:123"):
        self._default_chat_id = default_chat_id
        self._session_key = session_key

    def get_default_chat_id(self):
        return self._default_chat_id

    def get_session_key(self, chat_id):
        return self._session_key


class _FakeOrchestrator:
    def __init__(self):
        self.process_request = AsyncMock(return_value=None)


class _FakeSessionManager:
    def __init__(self, orchestrator=None):
        if orchestrator is not None:
            self.conversation_orchestrator = orchestrator


def _make_handler(last_history_id=None, default_chat_id=None, orchestrator=None):
    handler = GmailWebhookHandler.__new__(GmailWebhookHandler)
    handler._session_manager = _FakeSessionManager(orchestrator)
    handler._queue = asyncio.Queue()
    handler._worker_task = None
    handler._workspace_manager = _FakeWorkspaceManager(last_history_id)
    handler._chat_repo = _FakeChatRepo(default_chat_id)
    return handler


async def _run_worker_until_queue_drained(handler, timeout=1.0):
    task = asyncio.create_task(handler._worker_loop())
    try:
        await asyncio.wait_for(handler._queue.join(), timeout=timeout)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# --- handle_webhook: enqueue gate ------------------------------------------ #

def test_handle_webhook_missing_history_id_is_dropped():
    handler = _make_handler()
    handler.handle_webhook({"emailAddress": "someone@example.com"})
    assert handler._queue.empty()


def test_handle_webhook_valid_event_is_queued():
    handler = _make_handler()
    handler.handle_webhook({"historyId": "100", "emailAddress": "someone@example.com"})
    assert handler._queue.qsize() == 1


# --- worker loop: idempotency ------------------------------------------------ #

@pytest.mark.asyncio
async def test_worker_skips_redundant_notification_already_seen():
    orchestrator = _FakeOrchestrator()
    handler = _make_handler(last_history_id="100", orchestrator=orchestrator)
    handler.handle_webhook({"historyId": "100", "emailAddress": "x@y.com"})

    await _run_worker_until_queue_drained(handler)

    orchestrator.process_request.assert_not_called()


@pytest.mark.asyncio
async def test_worker_processes_new_notification_past_last_seen():
    orchestrator = _FakeOrchestrator()
    handler = _make_handler(last_history_id="100", default_chat_id="tgchat1", orchestrator=orchestrator)
    handler.handle_webhook({"historyId": "101", "emailAddress": "x@y.com"})

    await _run_worker_until_queue_drained(handler)

    orchestrator.process_request.assert_called_once()
    _, kwargs = orchestrator.process_request.call_args
    assert kwargs["context"].platform == "gmail"
    assert kwargs["context"].session_id == "tg:123"
    assert kwargs["category"] == "autonomous"
    assert "101" in kwargs["prompt"]


@pytest.mark.asyncio
async def test_worker_falls_back_to_generic_session_when_no_telegram_chat():
    orchestrator = _FakeOrchestrator()
    handler = _make_handler(last_history_id=None, default_chat_id=None, orchestrator=orchestrator)
    handler.handle_webhook({"historyId": "1", "emailAddress": "x@y.com"})

    await _run_worker_until_queue_drained(handler)

    _, kwargs = orchestrator.process_request.call_args
    assert kwargs["context"].session_id == "autonomous:gmail"


# --- worker loop: orchestrator not ready -> re-queue, don't drop ------------ #

@pytest.mark.asyncio
async def test_worker_requeues_when_orchestrator_not_ready():
    handler = _make_handler(last_history_id=None, orchestrator=None)  # no conversation_orchestrator attr
    handler.handle_webhook({"historyId": "1", "emailAddress": "x@y.com"})

    with patch("src.remote.webhooks.gmail_handler.asyncio.sleep", new=AsyncMock()):
        task = asyncio.create_task(handler._worker_loop())
        # It will loop forever re-queueing since orchestrator never becomes ready;
        # let it spin a few iterations, then confirm the item is still pending (not dropped).
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert handler._queue.qsize() >= 1, "event must be re-queued, never silently dropped"


# --- worker loop: exception during processing doesn't stall the queue ------ #

@pytest.mark.asyncio
async def test_worker_error_during_processing_does_not_stall_subsequent_events():
    orchestrator = _FakeOrchestrator()
    orchestrator.process_request.side_effect = [RuntimeError("boom"), None]
    handler = _make_handler(last_history_id=None, default_chat_id="c1", orchestrator=orchestrator)
    handler.handle_webhook({"historyId": "1", "emailAddress": "x@y.com"})
    handler.handle_webhook({"historyId": "2", "emailAddress": "x@y.com"})

    with patch("src.remote.webhooks.gmail_handler.asyncio.sleep", new=AsyncMock()):
        await _run_worker_until_queue_drained(handler, timeout=2.0)

    assert orchestrator.process_request.call_count == 2, (
        "an exception on the first event must not stall/crash the worker for the second"
    )


def test_is_actionable_email():
    # Promotions/spam/social should NOT be actionable
    assert GmailWebhookHandler._is_actionable_email({"labels": ["CATEGORY_PROMOTIONS"]}) is False
    assert GmailWebhookHandler._is_actionable_email({"labels": ["SPAM"]}) is False
    assert GmailWebhookHandler._is_actionable_email({"labels": ["CATEGORY_SOCIAL"]}) is False
    assert GmailWebhookHandler._is_actionable_email({"labels": ["CATEGORY_FORUMS"]}) is False

    # Automated senders should NOT be actionable
    assert GmailWebhookHandler._is_actionable_email({"from_email": "no-reply@walgreens.com", "subject": "refill delay"}) is False
    assert GmailWebhookHandler._is_actionable_email({"from_email": "newsletter@superhuman.com", "subject": "top ai news"}) is False
    assert GmailWebhookHandler._is_actionable_email({"from_email": "orders@amazon.com", "subject": "your order has shipped"}) is False

    # Real human emails SHOULD be actionable
    assert GmailWebhookHandler._is_actionable_email({
        "from_email": "professor.smith@university.edu",
        "from": "Professor Smith <professor.smith@university.edu>",
        "subject": "Office hours moved",
        "labels": ["INBOX", "UNREAD"]
    }) is True
    assert GmailWebhookHandler._is_actionable_email({
        "from_email": "jordan@example.com",
        "from": "Jordan <jordan@example.com>",
        "subject": "quick question about dinner",
        "labels": ["INBOX", "UNREAD"]
    }) is True
