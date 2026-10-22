"""HeartbeatService — autonomous cycle resilience (issue #216: cron/heartbeat full-flow gaps).

tests/scenarios/test_heartbeat_cron_flow.py already proves the real orchestrator path
end-to-end with live creds. These unit tests isolate the two "must never take down the
24/7 background service" guarantees: a bad Supabase checklist / orchestrator failure inside
_execute_heartbeat, and a broken reflection cycle inside _maybe_run_reflection.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.monitoring.heartbeat_service import HeartbeatService


def _config_get(overrides):
    defaults = {"reflection.enabled": False}
    defaults.update(overrides)
    return lambda key, default=None: defaults.get(key, default)


def _service():
    orchestrator = MagicMock()
    orchestrator.process_request = AsyncMock(return_value="HEARTBEAT_OK")
    service = HeartbeatService(orchestrator)
    # Resilience tests must exercise their intended path at any local hour.
    service._impulse_engine.is_quiet_hours = MagicMock(return_value=False)
    return service, orchestrator


# --- _execute_heartbeat ------------------------------------------------------ #

@pytest.mark.asyncio
async def test_execute_heartbeat_stays_silent_during_quiet_hours():
    svc, orchestrator = _service()
    svc._impulse_engine.is_quiet_hours.return_value = True

    await svc._execute_heartbeat()

    orchestrator.process_request.assert_not_called()
    assert svc._last_check is None


@pytest.mark.asyncio
async def test_execute_heartbeat_falls_back_to_default_checklist_when_supabase_empty():
    svc, orchestrator = _service()
    fake_supabase = MagicMock()
    fake_supabase.query.return_value = []  # no persona_config row

    with patch(
        "src.infrastructure.database.supabase_manager.get_supabase_manager",
        return_value=fake_supabase,
    ), patch(
        "src.infrastructure.repositories.telegram_chat_repository.TelegramChatRepository"
    ) as chat_repo_cls, patch.object(
        svc, "_get_default_checklist_text", return_value="- Check fictional test inbox"
    ) as default_checklist:
        chat_repo_cls.return_value.get_default_chat_id.return_value = None
        await svc._execute_heartbeat()

    orchestrator.process_request.assert_called_once()
    _, kwargs = orchestrator.process_request.call_args
    default_checklist.assert_called_once()
    assert "Check fictional test inbox" in kwargs["prompt"]
    assert kwargs["category"] == "autonomous"


@pytest.mark.asyncio
async def test_execute_heartbeat_survives_orchestrator_exception():
    """A failing turn (model down, tool crash, whatever) must not propagate out of
    _execute_heartbeat and kill the 24/7 service loop."""
    svc, orchestrator = _service()
    orchestrator.process_request.side_effect = RuntimeError("model unreachable")
    fake_supabase = MagicMock()
    fake_supabase.query.return_value = [{"content": "- Check email\n"}]

    with patch(
        "src.infrastructure.database.supabase_manager.get_supabase_manager",
        return_value=fake_supabase,
    ), patch(
        "src.infrastructure.repositories.telegram_chat_repository.TelegramChatRepository"
    ) as chat_repo_cls:
        chat_repo_cls.return_value.get_default_chat_id.return_value = None
        await svc._execute_heartbeat()  # must not raise

    assert svc._last_check is not None


@pytest.mark.asyncio
async def test_execute_heartbeat_survives_supabase_query_exception():
    svc, orchestrator = _service()
    with patch(
        "src.infrastructure.database.supabase_manager.get_supabase_manager",
        side_effect=ConnectionError("supabase down"),
    ):
        await svc._execute_heartbeat()  # must not raise

    orchestrator.process_request.assert_not_called()


# --- _maybe_run_reflection ---------------------------------------------------- #

@pytest.mark.asyncio
async def test_reflection_noop_when_disabled():
    svc, _ = _service()
    from src.shared.config_manager import ConfigManager
    with patch.object(ConfigManager, "get", staticmethod(_config_get({"reflection.enabled": False}))), \
         patch("src.core.intelligence.reflection.propose_lessons", new=AsyncMock()) as propose:
        await svc._maybe_run_reflection()

    propose.assert_not_called()


@pytest.mark.asyncio
async def test_reflection_skips_before_interval_elapsed():
    svc, _ = _service()
    svc._last_reflection_ts = time.time()  # "just ran"
    from src.shared.config_manager import ConfigManager
    with patch.object(
        ConfigManager, "get",
        staticmethod(_config_get({"reflection.enabled": True, "reflection.interval_hours": 24})),
    ), patch("src.core.intelligence.reflection.propose_lessons", new=AsyncMock()) as propose:
        await svc._maybe_run_reflection()

    propose.assert_not_called()


# --- live daemon-enabled toggle (#473: GUI toggle must stop a running loop) -- #

@pytest.mark.asyncio
async def test_heartbeat_loop_skips_execution_when_daemon_disabled_live():
    """Toggling daemons.heartbeat off mid-session (no restart) must stop the loop
    from doing any work on its very next tick, not just at the next process boot."""
    svc, _ = _service()
    svc._running = True

    ticks = {"n": 0}

    async def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            svc._running = False  # stop the while-loop after a few ticks

    from src.shared.config_manager import ConfigManager
    with patch("asyncio.sleep", new=fake_sleep), \
         patch.object(
             ConfigManager, "get",
             staticmethod(_config_get({"daemons.heartbeat_interval_minutes": 1})),
         ), \
         patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=False), \
         patch.object(svc, "_execute_heartbeat", new=AsyncMock()) as exec_mock, \
         patch.object(svc, "_maybe_run_reflection", new=AsyncMock()) as reflect_mock, \
         patch.object(svc, "_maybe_run_sleep_cognify", new=AsyncMock()), \
         patch.object(svc, "_maybe_run_scavenger", new=AsyncMock()):
        await svc._heartbeat_loop()

    exec_mock.assert_not_called()
    reflect_mock.assert_not_called()
    assert ticks["n"] == 3, "loop must keep ticking (task stays alive) while paused"


@pytest.mark.asyncio
async def test_heartbeat_loop_executes_when_daemon_enabled_live():
    """Sanity counterpart: with the flag live-enabled, the loop still does its work
    (proves the new check doesn't silently swallow the enabled case)."""
    svc, _ = _service()
    svc._running = True

    ticks = {"n": 0}

    async def fake_sleep(_):
        ticks["n"] += 1
        # interval=1 minute -> tick_mod = (1*60)//5 = 12, so the 12th tick fires.
        if ticks["n"] >= 12:
            svc._running = False

    from src.shared.config_manager import ConfigManager
    with patch("asyncio.sleep", new=fake_sleep), \
         patch.object(
             ConfigManager, "get",
             staticmethod(_config_get({"daemons.heartbeat_interval_minutes": 1})),
         ), \
         patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=True), \
         patch.object(svc, "_execute_heartbeat", new=AsyncMock()) as exec_mock, \
         patch.object(svc, "_maybe_run_reflection", new=AsyncMock()) as reflect_mock, \
         patch.object(svc, "_maybe_run_sleep_cognify", new=AsyncMock()), \
         patch.object(svc, "_maybe_run_scavenger", new=AsyncMock()):
        await svc._heartbeat_loop()

    exec_mock.assert_called_once()
    assert reflect_mock.call_count == 12, "reflection is checked every tick regardless of heartbeat interval"


def test_get_status_online_when_running_and_daemon_enabled():
    svc, _ = _service()
    svc._running = True
    with patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=True):
        assert svc.get_status()["status"] == "online"


def test_get_status_paused_when_running_but_daemon_disabled():
    """This is the GUI-visible signal that the toggle took effect without a restart:
    the asyncio task is still alive ('running'), but Automations shows it paused."""
    svc, _ = _service()
    svc._running = True
    with patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=False):
        assert svc.get_status()["status"] == "paused"


def test_get_status_offline_when_not_running():
    svc, _ = _service()
    svc._running = False
    with patch("src.core.monitoring.daemon_status.daemon_enabled", return_value=True):
        assert svc.get_status()["status"] == "offline"


@pytest.mark.asyncio
async def test_reflection_survives_propose_lessons_exception():
    svc, _ = _service()
    svc._last_reflection_ts = 0.0  # interval elapsed
    from src.shared.config_manager import ConfigManager
    with patch.object(
        ConfigManager, "get",
        staticmethod(_config_get({"reflection.enabled": True, "reflection.interval_hours": 24})),
    ), patch(
        "src.core.intelligence.reflection.propose_lessons",
        new=AsyncMock(side_effect=RuntimeError("judge model unreachable")),
    ):
        await svc._maybe_run_reflection()  # must not raise

    assert svc._last_reflection_ts > 0, "timestamp must be marked BEFORE running so a slow/failing run can't double-fire"
