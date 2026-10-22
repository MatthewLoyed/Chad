"""The master switch pauses background entry points without changing manual chat."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from contextlib import asynccontextmanager

import pytest

from src.application.commands.automation_commands import UpdateDaemonSettingsCommand
from src.core.monitoring import daemon_status
from src.core.orchestration.conversation_orchestrator import ConversationOrchestrator, TurnInitiator
from src.infrastructure.repositories.config_repository import ConfigRepository
from src.shared.config_manager import ConfigManager
from src.shared.context import RequestContext


@pytest.mark.asyncio
async def test_master_toggle_persists_only_master_preference(monkeypatch):
    monkeypatch.delenv('CHAD_DAEMONS_ENABLED', raising=False)
    save = Mock(return_value=True)
    monkeypatch.setattr(ConfigRepository, 'update_preference', save)
    monkeypatch.setattr(ConfigManager, 'load_config', Mock())
    monkeypatch.setattr(daemon_status, 'get_daemon_snapshot', lambda _: [])
    for enabled in (False, True):
        command = UpdateDaemonSettingsCommand('master', enabled=enabled)
        assert command.validate() == (True, None)
        assert (await command.execute(SimpleNamespace()))['success']
        save.assert_called_with('daemons.enabled', enabled)
    assert save.call_count == 2


@pytest.mark.asyncio
async def test_environment_override_is_reported_instead_of_false_success(monkeypatch):
    monkeypatch.setenv('CHAD_DAEMONS_ENABLED', 'false')
    result = await UpdateDaemonSettingsCommand('master', enabled=True).execute(SimpleNamespace())
    assert not result['success']
    assert 'launch configuration' in result['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('initiator', [TurnInitiator.SYSTEM, TurnInitiator.SKILL, TurnInitiator.WEBHOOK, TurnInitiator.USER])
async def test_paused_master_blocks_autonomous_requests_but_allows_user(monkeypatch, initiator):
    monkeypatch.setattr(daemon_status, 'daemons_master_enabled', lambda: False)
    run = AsyncMock(return_value='manual answer')
    orchestrator = SimpleNamespace(_process_request_locked=run)
    result = await ConversationOrchestrator.process_request(
        orchestrator, 'test request', initiator=initiator,
        context=RequestContext(platform='gui', session_id='test', delivery='gui'),
    )
    assert result == ('manual answer' if initiator == TurnInitiator.USER else '')
    assert run.await_count == (1 if initiator == TurnInitiator.USER else 0)


def test_paused_webhook_is_not_enqueued(monkeypatch):
    from src.remote.webhooks.gmail_handler import GmailWebhookHandler
    monkeypatch.setattr(daemon_status, 'daemon_enabled', lambda _: False)
    handler = SimpleNamespace(_queue=asyncio.Queue())
    GmailWebhookHandler.handle_webhook(handler, {'historyId': '123'})
    assert handler._queue.empty()


@pytest.mark.asyncio
async def test_request_queued_before_pause_rechecks_master_after_waiting(monkeypatch):
    lock = asyncio.Lock()
    await lock.acquire()
    waiting = asyncio.Event()
    enabled = True

    @asynccontextmanager
    async def guard(**_):
        waiting.set()
        yield

    monkeypatch.setattr(daemon_status, 'daemons_master_enabled', lambda: enabled)
    monkeypatch.setattr(ConfigManager, 'is_local_model_active', lambda: False)
    orchestrator = SimpleNamespace(
        _session_lock_mgr=SimpleNamespace(get=lambda _: lock),
        _concurrency_gate=SimpleNamespace(turn_guard=guard),
    )
    task = asyncio.create_task(ConversationOrchestrator._process_request_locked(
        orchestrator, 'background request', 'autonomous', TurnInitiator.SYSTEM,
        RequestContext(platform='system', session_id='queued'), None, None, 'telegram',
    ))
    await asyncio.wait_for(waiting.wait(), 1)
    enabled = False
    lock.release()
    assert await asyncio.wait_for(task, 1) == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['_maybe_run_reflection', '_maybe_run_sleep_cognify'])
async def test_heartbeat_subcycles_do_not_start_after_master_is_paused(monkeypatch, method):
    from src.core.monitoring.heartbeat_service import HeartbeatService
    monkeypatch.setattr(daemon_status, 'daemons_master_enabled', lambda: False)
    get_config = Mock(side_effect=AssertionError('No subcycle work should start'))
    monkeypatch.setattr(ConfigManager, 'get', get_config)
    service = SimpleNamespace(_logger=Mock())
    await getattr(HeartbeatService, method)(service)
    get_config.assert_not_called()
    service._logger.error.assert_not_called()
    service._logger.warning.assert_not_called()
