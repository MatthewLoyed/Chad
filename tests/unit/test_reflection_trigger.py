"""Unit tests for thumbs-down reflection trigger wiring."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.events.events import TurnFeedbackEvent
from src.core.intelligence.reflection_trigger import register_reflection_triggers


class _FakeEventBus:
    def __init__(self):
        self._handlers = {}

    def subscribe(self, event_type, handler):
        self._handlers[event_type] = handler


@pytest.fixture
def event_bus():
    return _FakeEventBus()


def test_register_reflection_triggers_idempotent(event_bus):
    register_reflection_triggers(event_bus)
    first_handler = event_bus._handlers.get(TurnFeedbackEvent)
    register_reflection_triggers(event_bus)
    assert event_bus._handlers.get(TurnFeedbackEvent) is first_handler


@pytest.mark.asyncio
async def test_thumbs_down_schedules_dry_run_reflection(event_bus):
    register_reflection_triggers(event_bus)
    handler = event_bus._handlers[TurnFeedbackEvent]

    with patch(
        "src.core.intelligence.reflection.propose_lessons",
        new_callable=AsyncMock,
    ) as mock_propose, patch(
        "src.core.intelligence.reflection._render_proposal",
        return_value="# proposal",
    ):
        handler(
            TurnFeedbackEvent(
                session_id="s1",
                turn_id="turn_abc",
                rating="negative",
            )
        )
        await asyncio.sleep(0.05)

    mock_propose.assert_awaited_once()


def test_judge_model_prefers_active_supervisor_model(monkeypatch):
    from src.core.intelligence.reflection_trigger import _judge_model
    from src.shared.config_manager import ConfigManager

    monkeypatch.delenv("CHAD_REFLECTION_JUDGE_MODEL", raising=False)
    monkeypatch.setattr(ConfigManager, "get_active_model", staticmethod(lambda: "prism-ml/bonsai-27b"))
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, default=None: "prism-ml/bonsai-27b" if k == "reflection.model" else default))

    assert _judge_model() == "prism-ml/bonsai-27b"


def test_is_turn_reflection_candidate_detects_tool_failure():
    from src.core.intelligence.reflection_trigger import _is_turn_reflection_candidate
    from datetime import datetime, timezone

    mock_db = MagicMock()
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_db.query.side_effect = lambda table, **kwargs: (
        [{"outcome": "failure", "created_at": now_iso}] if table == "tool_outcome_log" else []
    )

    assert _is_turn_reflection_candidate(mock_db) is True


def test_is_turn_reflection_candidate_detects_user_correction():
    from src.core.intelligence.reflection_trigger import _is_turn_reflection_candidate

    mock_db = MagicMock()
    mock_db.query.side_effect = lambda table, **kwargs: (
        [] if table == "tool_outcome_log" else [
            {"role": "user", "content": "No, download the lectures instead, not assignments."}
        ]
    )

    assert _is_turn_reflection_candidate(mock_db) is True


def test_is_turn_reflection_candidate_skips_normal_turns():
    from src.core.intelligence.reflection_trigger import _is_turn_reflection_candidate

    mock_db = MagicMock()
    mock_db.query.side_effect = lambda table, **kwargs: (
        [] if table == "tool_outcome_log" else [
            {"role": "user", "content": "Thanks Chad, that looks great."}
        ]
    )

    assert _is_turn_reflection_candidate(mock_db) is False


@pytest.mark.asyncio
async def test_turn_complete_proposes_lessons_without_auto_applying(event_bus, monkeypatch):
    from src.application.events.events import TurnCompleteEvent
    from src.core.intelligence.reflection import LessonProposal, ReflectionResult
    from src.core.intelligence import reflection_trigger

    monkeypatch.setattr(reflection_trigger, '_reflection_task', None)

    register_reflection_triggers(event_bus)
    handler = event_bus._handlers[TurnCompleteEvent]

    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    mock_supabase.query.side_effect = lambda table, **kwargs: (
        [{"outcome": "failure", "created_at": "2026-10-14T21:00:00Z"}] if table == "tool_outcome_log" else []
    )

    fake_lesson = {
        "text": "Canvas lecture files are published in Modules.",
        "scope": "tools",
        "confidence": 0.95,
    }
    fake_proposal = LessonProposal(
        reflection=ReflectionResult(lessons=[fake_lesson]),
        admitted=[fake_lesson],
    )

    with patch("src.core.intelligence.reflection_trigger._judge_model", return_value='fictional-test-model'), patch("src.shared.config_manager.ConfigManager.get",
               side_effect=lambda key, default=None: True if key == 'reflection.propose_on_failure' else default), patch(
        "src.core.intelligence.reflection_trigger._is_turn_reflection_candidate",
        return_value=True,
    ), patch(
        "src.core.intelligence.reflection.propose_lessons",
        new_callable=AsyncMock,
        return_value=fake_proposal,
    ) as mock_propose, patch(
        "src.core.intelligence.reflection_lessons_store.persist_lesson_and_publish",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_persist, patch(
        "src.infrastructure.database.supabase_manager.get_supabase_manager",
        return_value=mock_supabase,
    ):
        handler(TurnCompleteEvent(turn_id="turn_instant_123"))
        await reflection_trigger._reflection_task

    mock_propose.assert_awaited_once()
    mock_persist.assert_awaited_once_with(mock_supabase, fake_lesson, "lesson", auto_apply=False)
