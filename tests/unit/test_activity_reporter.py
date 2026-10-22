"""
Unit tests for AutonomousActivityReporter.

Covers:
- record_run is a no-op for USER initiator
- record_run calls record_llm_turn for non-USER initiator
- surface_to_dashboard is a no-op for USER initiator
- surface_to_dashboard is a no-op when tools_executed is empty
- surface_to_dashboard calls dashboard_feed.append_entry for non-USER + tools
- Both methods are best-effort: exceptions are swallowed, not re-raised
"""

import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.conversation.activity_reporter import AutonomousActivityReporter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER = "user"
_SYSTEM = "system"
_WEBHOOK = "webhook"


# ---------------------------------------------------------------------------
# record_run — USER is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_run_user_noop():
    reporter = AutonomousActivityReporter()
    mock_record = MagicMock()

    import sys
    import types
    fake_mod = types.ModuleType("src.core.monitoring.autonomous_run_log")
    fake_mod.record_llm_turn = mock_record
    orig = sys.modules.get("src.core.monitoring.autonomous_run_log")
    sys.modules["src.core.monitoring.autonomous_run_log"] = fake_mod
    try:
        await reporter.record_run(
            initiator=_USER,
            category="chat",
            user_input="hello",
            model_response="hi",
            tools_executed=[],
            duration_sec=0.1,
        )
    finally:
        if orig is None:
            sys.modules.pop("src.core.monitoring.autonomous_run_log", None)
        else:
            sys.modules["src.core.monitoring.autonomous_run_log"] = orig

    mock_record.assert_not_called()



@pytest.mark.asyncio
async def test_record_run_non_user_calls_log():
    reporter = AutonomousActivityReporter()
    mock_record = MagicMock()

    with patch.dict(
        "sys.modules",
        {"src.core.monitoring.autonomous_run_log": MagicMock(record_llm_turn=mock_record)},
    ):
        await reporter.record_run(
            initiator=_SYSTEM,
            category="agent",
            user_input=None,
            model_response="done",
            tools_executed=["read_file"],
            duration_sec=1.5,
        )

    mock_record.assert_called_once()
    call_kwargs = mock_record.call_args.kwargs
    assert call_kwargs["initiator"] == _SYSTEM
    assert call_kwargs["tools_executed"] == ["read_file"]


@pytest.mark.asyncio
async def test_record_run_exception_swallowed():
    reporter = AutonomousActivityReporter()

    with patch.dict(
        "sys.modules",
        {"src.core.monitoring.autonomous_run_log": MagicMock(record_llm_turn=MagicMock(side_effect=RuntimeError("db down")))},
    ):
        # Must NOT raise
        await reporter.record_run(
            initiator=_SYSTEM,
            category="agent",
            user_input=None,
            model_response=None,
            tools_executed=[],
            duration_sec=0.0,
        )


# ---------------------------------------------------------------------------
# surface_to_dashboard — USER is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surface_user_noop():
    reporter = AutonomousActivityReporter()
    mock_append = AsyncMock()
    mock_feed = MagicMock(append_entry=mock_append)

    with patch.dict(
        "sys.modules",
        {"src.core.intelligence.dashboard_feed": mock_feed,
         "src.core.intelligence": MagicMock(dashboard_feed=mock_feed)},
    ):
        await reporter.surface_to_dashboard(
            initiator=_USER,
            category="chat",
            session_key="s1",
            tools_executed=["read_file"],
            model_response="done",
        )

    mock_append.assert_not_called()


# ---------------------------------------------------------------------------
# surface_to_dashboard — no tools is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surface_no_tools_noop():
    reporter = AutonomousActivityReporter()
    mock_append = AsyncMock()
    mock_feed = MagicMock(append_entry=mock_append)

    with patch.dict(
        "sys.modules",
        {"src.core.intelligence.dashboard_feed": mock_feed,
         "src.core.intelligence": MagicMock(dashboard_feed=mock_feed)},
    ):
        await reporter.surface_to_dashboard(
            initiator=_SYSTEM,
            category="agent",
            session_key="s1",
            tools_executed=[],
            model_response="nothing done",
        )

    mock_append.assert_not_called()


# ---------------------------------------------------------------------------
# surface_to_dashboard — non-USER + tools → append_entry called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surface_non_user_with_tools_appends_entry():
    reporter = AutonomousActivityReporter()
    mock_append = AsyncMock()
    mock_feed = MagicMock(append_entry=mock_append)

    with patch.dict(
        "sys.modules",
        {"src.core.intelligence.dashboard_feed": mock_feed,
         "src.core.intelligence": MagicMock(dashboard_feed=mock_feed)},
    ):
        await reporter.surface_to_dashboard(
            initiator=_WEBHOOK,
            category="agent",
            session_key="s1",
            tools_executed=["send_email", "read_file"],
            model_response="I sent the email",
        )

    mock_append.assert_awaited_once()
    call_kwargs = mock_append.call_args.kwargs
    assert call_kwargs["kind"] == "status"
    assert "send_email" in call_kwargs["title"]
    assert call_kwargs["metadata"]["initiator"] == _WEBHOOK


@pytest.mark.asyncio
async def test_surface_exception_swallowed():
    reporter = AutonomousActivityReporter()
    mock_append = AsyncMock(side_effect=RuntimeError("feed down"))
    mock_feed = MagicMock(append_entry=mock_append)

    with patch.dict(
        "sys.modules",
        {"src.core.intelligence.dashboard_feed": mock_feed,
         "src.core.intelligence": MagicMock(dashboard_feed=mock_feed)},
    ):
        # Must NOT raise
        await reporter.surface_to_dashboard(
            initiator=_SYSTEM,
            category="agent",
            session_key=None,
            tools_executed=["run_script"],
            model_response=None,
        )
