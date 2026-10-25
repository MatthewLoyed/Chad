"""
Unit Tests for CuriosityService, ImpulseEngine, and ManageCuriosity Tool.
"""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.autonomy.curiosity_service import CuriosityService
from src.core.autonomy.impulse_engine import ImpulseEngine
from src.tools.system.utilities.manage_curiosity import ManageCuriosity


class InMemoryStateRepository:
    """Mock state repository for fast isolated unit testing."""

    def __init__(self):
        self._data = {}

    def get_value(self, key: str, default=None):
        return self._data.get(key, default)

    def set_value(self, key: str, value):
        self._data[key] = value
        return True


# --- CuriosityService Tests --------------------------------------------------- #

@pytest.mark.asyncio
async def test_curiosity_service_add_and_pop():
    repo = InMemoryStateRepository()
    service = CuriosityService(state_repo=repo)

    # Mock verify_url to return True for good URLs
    with patch.object(service, "verify_url", new=AsyncMock(return_value=True)):
        added = await service.add_item(
            url="https://github.com/astral-sh/uv",
            title="uv - Fast Python package installer",
            category="tech",
            source="github",
            summary="Extremely fast python manager in Rust.",
        )
        assert added is True

        status = service.get_status()
        assert status["available_count"] == 1

        # Pop next item
        item = service.pop_next_curiosity()
        assert item is not None
        assert item["url"] == "https://github.com/astral-sh/uv"
        assert item["sent_at"] is not None

        # Popping again should return None because it was marked sent
        second_item = service.pop_next_curiosity()
        assert second_item is None


@pytest.mark.asyncio
async def test_curiosity_service_duplicate_prevention():
    repo = InMemoryStateRepository()
    service = CuriosityService(state_repo=repo)

    with patch.object(service, "verify_url", new=AsyncMock(return_value=True)):
        added1 = await service.add_item(
            url="https://youtube.com/shorts/test12345",
            title="Spikeball trick shot",
            category="spikeball",
        )
        assert added1 is True

        # Duplicate URL should be rejected
        added2 = await service.add_item(
            url="https://youtube.com/shorts/test12345",
            title="Duplicate shot",
            category="spikeball",
        )
        assert added2 is False


@pytest.mark.asyncio
async def test_curiosity_service_rejects_invalid_url():
    repo = InMemoryStateRepository()
    service = CuriosityService(state_repo=repo)

    # Mock verify_url to return False
    with patch.object(service, "verify_url", new=AsyncMock(return_value=False)):
        added = await service.add_item(
            url="https://broken-404-link.com/nonexistent",
            title="Broken link",
            category="tech",
        )
        assert added is False
        assert len(service.get_available_items()) == 0


def test_curiosity_feedback_tuning():
    repo = InMemoryStateRepository()
    service = CuriosityService(state_repo=repo)

    initial_tech = service.get_affinity()["tech"]

    # Positive feedback
    service.record_feedback("lmao that's fire bro", category="tech")
    boosted_tech = service.get_affinity()["tech"]
    assert boosted_tech > initial_tech

    # Negative feedback
    service.record_feedback("mid honestly", category="tech")
    lowered_tech = service.get_affinity()["tech"]
    assert lowered_tech < boosted_tech


# --- ImpulseEngine Tests ----------------------------------------------------- #

def test_impulse_engine_quiet_hours():
    repo = InMemoryStateRepository()
    engine = ImpulseEngine(state_repo=repo)

    # 2:00 AM (sleep hours) -> quiet
    late_night = datetime(2026, 10, 16, 2, 0)
    assert engine.is_quiet_hours(late_night) is True

    # 14:00 (2:00 PM) -> awake hours
    daytime = datetime(2026, 10, 16, 14, 0)
    assert engine.is_quiet_hours(daytime) is False


def test_impulse_engine_chill_vs_busy_events():
    repo = InMemoryStateRepository()
    engine = ImpulseEngine(state_repo=repo)

    daytime = datetime(2026, 10, 16, 14, 0)

    # Chill event (class/lecture) -> Matthew doesn't mind a text
    chill_events = [{"summary": "CS 6120 Machine Learning Class"}]
    with patch.object(engine, "is_quiet_hours", return_value=False), \
         patch.object(engine, "_get_last_spontaneous_ts", return_value=0.0):
        can_trigger, reason, _ = engine.evaluate(active_events=chill_events, now_dt=daytime)
        assert can_trigger is True
        assert reason == "spontaneous_impulse"

    # Blocked event (interview / wedding / exam) -> quiet
    busy_events = [{"summary": "Job Interview with Team"}]
    with patch.object(engine, "is_quiet_hours", return_value=False):
        can_trigger, reason, _ = engine.evaluate(active_events=busy_events, now_dt=daytime)
        assert can_trigger is False
        assert "busy_event" in reason


def test_impulse_engine_post_event_spike_and_dedup():
    repo = InMemoryStateRepository()
    engine = ImpulseEngine(state_repo=repo)

    daytime = datetime(2026, 10, 16, 14, 0)
    ended_event = {"id": "cal_ev_gym_99", "summary": "Gym Workout"}

    with patch.object(engine, "is_quiet_hours", return_value=False):
        # First time: triggers post-event spike
        can_trigger, reason, ctx = engine.evaluate(recent_ended_events=[ended_event], now_dt=daytime)
        assert can_trigger is True
        assert reason == "post_event_spike"
        assert ctx["id"] == "cal_ev_gym_99"

        # Record as checked
        engine.record_event_checked("cal_ev_gym_99")
        assert engine.is_event_checked("cal_ev_gym_99") is True

        # Second time: should NOT trigger post_event_spike for same event
        can_trigger2, reason2, _ = engine.evaluate(recent_ended_events=[ended_event], now_dt=daytime)
        assert reason2 != "post_event_spike"


def test_impulse_engine_cooldown():
    repo = InMemoryStateRepository()
    engine = ImpulseEngine(state_repo=repo)

    # Simulate outreach just sent
    engine.record_spontaneous_sent()

    with patch.object(engine, "is_quiet_hours", return_value=False):
        can_trigger, reason, _ = engine.evaluate()
        assert can_trigger is False
        assert "in_cooldown" in reason


# --- ManageCuriosity Tool Tests ----------------------------------------------- #

@pytest.mark.asyncio
async def test_manage_curiosity_tool_get_and_status():
    tool = ManageCuriosity()
    tool.curiosity_service = CuriosityService(state_repo=InMemoryStateRepository())

    # Mock add an item
    with patch.object(tool.curiosity_service, "verify_url", new=AsyncMock(return_value=True)):
        await tool.curiosity_service.add_item(
            url="https://github.com/rust-lang/rust",
            title="Rust Programming Language",
            category="tech",
        )

        # Status check
        status_res = await tool.execute(action="status")
        assert status_res["success"] is True
        assert status_res["status"]["available_count"] == 1

        # Get next item
        get_res = await tool.execute(action="get_next")
        assert get_res["success"] is True
        assert get_res["item"]["url"] == "https://github.com/rust-lang/rust"

        # Get next again when empty
        empty_res = await tool.execute(action="get_next")
        assert empty_res["success"] is False

@pytest.mark.asyncio
async def test_scavenger_publishes_finds_without_consuming_stash():
    repo = InMemoryStateRepository()
    service = CuriosityService(state_repo=repo)
    with patch('src.core.discovery.pipeline.ContentDiscoveryPipeline') as pipeline_cls:
        discover = pipeline_cls.return_value.discover = AsyncMock(return_value=[])
        assert await service.scavenge() == 0
        assert discover.await_args.kwargs['push_to_dashboard'] is True
        assert discover.await_args.kwargs['push_to_stash'] is True
        assert discover.await_args.kwargs['verify'] is True
        assert service._get_sent_history() == {}

@pytest.mark.asyncio
async def test_curiosity_add_receipt_does_not_claim_feed_publication():
    tool = ManageCuriosity()
    tool.curiosity_service = MagicMock()
    tool.curiosity_service.add_item = AsyncMock(return_value=True)
    with patch('src.core.intelligence.dashboard_feed.append_entry', new=AsyncMock()) as publish:
        result = await tool.execute(action='add', url='https://example.com/article', title='A useful discovery')
    assert result['success'] is True
    assert result['feed_published'] is False
    assert 'entry_id' not in result
    assert 'update_dashboard' in result['next_action']
    assert 'search_for_tool' in tool.get_schema()['description']
    publish.assert_not_awaited()
