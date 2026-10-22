"""
Unit Tests for 2026-Backed Proactive Memory Extraction & Repetition Tracking.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.autonomy.proactive_memory import (
    ProactiveMemoryService,
    is_memory_candidate,
    _extract_topic_signature,
)
from src.core.intelligence.memory_write_gate import WriteDecision


class InMemoryStateRepository:
    def __init__(self):
        self._data = {}

    def get_value(self, key: str, default=None):
        return self._data.get(key, default)

    def set_value(self, key: str, value):
        self._data[key] = value
        return True


# --- Tier 1 Heuristic Tests -------------------------------------------------- #

def test_is_memory_candidate_identifies_personal_facts():
    # Preferences and first-person assertions
    assert is_memory_candidate("i started drinking oat milk instead of whole milk") is True
    assert is_memory_candidate("i love working out with calisthenics") is True
    assert is_memory_candidate("i prefer rust over python for cli tools") is True
    assert is_memory_candidate("i hate morning meetings before 10am") is True
    assert is_memory_candidate("i bought a new keyboard yesterday") is True

    # Relationship cues work independently of any maintainer's contacts.
    assert is_memory_candidate("my wedding venue search starts tomorrow") is True
    assert is_memory_candidate("my dog Scout was barking all night") is True
    assert is_memory_candidate("my friend Jordan is coming over") is True

    # Directives to Chad
    assert is_memory_candidate("always give me the terminal command directly") is True
    assert is_memory_candidate("from now on keep responses under 2 sentences") is True
    assert is_memory_candidate("remember that the repo is on github") is True


def test_is_memory_candidate_rejects_generic_queries():
    assert is_memory_candidate("what is git status") is False
    assert is_memory_candidate("run pytest on tests/unit") is False
    assert is_memory_candidate("show me the weather in new york") is False
    assert is_memory_candidate("how does a binary search tree work") is False
    assert is_memory_candidate("Jordan arrived in Exampleville yesterday") is False
    assert is_memory_candidate("") is False
    assert is_memory_candidate(None) is False


def test_topic_signature_extraction():
    sig = _extract_topic_signature("Can you help me check the calendar for tomorrow morning please?")
    assert "calendar" in sig
    assert "tomorrow" in sig
    assert "morning" in sig
    assert "can" not in sig
    assert "you" not in sig


# --- Tier 4 Repetition Tracking Tests ---------------------------------------- #

def test_repetition_tracking_synthesizes_habit_at_threshold():
    repo = InMemoryStateRepository()
    service = ProactiveMemoryService(state_repo=repo)

    query = "check my calendar for tomorrow morning"

    # Turn 1: 1st occurrence -> no habit yet
    habit1 = service.track_repetition(query)
    assert habit1 is None

    # Turn 2: 2nd occurrence -> no habit yet
    habit2 = service.track_repetition(query)
    assert habit2 is None

    # Turn 3: 3rd occurrence -> habit detected!
    habit3 = service.track_repetition(query)
    assert habit3 is not None
    assert "calendar" in habit3
    assert "Frequently" in habit3


# --- Tier 2 & 3 Distillation & Ingestion Tests ------------------------------- #

@pytest.mark.asyncio
async def test_distill_facts_parses_json_response():
    repo = InMemoryStateRepository()
    service = ProactiveMemoryService(state_repo=repo)

    mock_resp = MagicMock()
    mock_resp.text = '{"facts": ["Matthew prefers oat milk over dairy milk."]}'

    mock_adapter = MagicMock()
    mock_adapter.generate = AsyncMock(return_value=mock_resp)

    with patch("evals.judges.build_judge_adapter", return_value=mock_adapter):
        facts = await service.distill_facts(
            user_text="i started drinking oat milk instead of whole milk",
            assistant_text="Good call, oat milk is solid!",
        )
        assert len(facts) == 1
        assert facts[0] == "Matthew prefers oat milk over dairy milk."


@pytest.mark.asyncio
async def test_process_turn_returns_proposals_without_writing_memory():
    repo = InMemoryStateRepository()
    service = ProactiveMemoryService(state_repo=repo)

    # Mock distillation
    distilled_fact = "Matthew prefers terminal commands directly without explanations."
    service.distill_facts = AsyncMock(return_value=[distilled_fact])

    # Mock GraphRAGClient and MemoryWriteGate
    mock_graph = MagicMock()
    mock_graph.ingest = AsyncMock(return_value=True)
    service._get_graph_client = AsyncMock(return_value=mock_graph)

    mock_decision = WriteDecision(accepted=[distilled_fact], skipped=[], conflicts=[])
    mock_gate = MagicMock()
    mock_gate.evaluate = AsyncMock(return_value=mock_decision)

    with patch("src.core.intelligence.memory_write_gate.MemoryWriteGate.from_config", return_value=mock_gate):
        saved = await service.process_turn(
            user_text="always give me the terminal command directly",
            assistant_text="Got it, will do.",
        )
        assert len(saved) == 1
        assert saved[0] == distilled_fact
        mock_graph.ingest.assert_not_called()


# --- Event Bus Subscription Tests -------------------------------------------- #

@pytest.mark.asyncio
async def test_event_bus_does_not_extract_personal_memory_without_approval():
    from src.application.events import EventBus, TurnStarted, TurnCompleteEvent
    from src.core.intelligence.reflection_trigger import register_reflection_triggers

    bus = EventBus()
    register_reflection_triggers(bus)

    with patch("src.core.autonomy.proactive_memory.ProactiveMemoryService.process_turn", new=AsyncMock()) as mock_process:
        # User starts turn
        bus.publish(TurnStarted(turn_id="turn-abc-123", user_input="i love playing spikeball"))

        # Turn completes
        bus.publish(TurnCompleteEvent(turn_id="turn-abc-123", response="Spikeball is awesome!"))

        # Allow async task in background to execute
        await asyncio.sleep(0.05)

        mock_process.assert_not_called()
