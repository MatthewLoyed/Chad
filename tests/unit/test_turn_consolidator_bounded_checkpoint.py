"""Bounded-checkpoint coverage for issue #216 ("long conversation, no memory
leak, checkpoint size stays bounded").

TurnConsolidator.preflight() is the mechanism that keeps a long-running
conversation's checkpoint bounded: once projected tokens cross 70% of the
context window it runs the Ship's Log compaction loop, which archives old
messages and resets the LangGraph checkpointer for the thread (see
run_compaction, consolidation.py:94). These tests exercise that trigger and
prove the checkpoint gets reset rather than growing unboundedly across a long
(50+ turn) conversation, without needing to actually drive 50 live turns.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.orchestration.turn.consolidation import TurnConsolidator


def _make_history(n_messages: int, content_repeat: int = 500):
    """A history long/verbose enough to blow past any reasonable token threshold."""
    filler = "word " * content_repeat
    return [
        {"id": i, "role": "user" if i % 2 == 0 else "assistant", "content": filler}
        for i in range(n_messages)
    ]


@pytest.mark.asyncio
async def test_preflight_triggers_compaction_and_resets_checkpoint_when_over_threshold():
    consolidation_manager = MagicMock()
    consolidation_manager.limits = {"hard": 1000}  # small hard cap so our filler history exceeds 70%
    consolidation_manager.split_history_by_tokens.return_value = (
        _make_history(10),  # to_compact
        _make_history(2),   # to_keep
    )
    consolidation_manager.perform_consolidation_with_safeguards = AsyncMock(
        return_value={"wake_message": "Summary of earlier turns.", "summary": {}, "safeguards": None}
    )
    consolidation_manager._persist_data_with_safeguards = MagicMock()

    history_loader = MagicMock()
    history_loader.load = AsyncMock(return_value=_make_history(2))

    langgraph_engine = MagicMock()
    langgraph_engine.clear_checkpoints = AsyncMock()

    adapter = MagicMock()

    consolidator = TurnConsolidator(
        consolidation_manager=consolidation_manager,
        event_bus=None,
        history_loader=history_loader,
    )

    with patch(
        "src.infrastructure.repositories.chat_history_repository.ChatHistoryRepository"
    ) as MockRepo:
        repo_instance = MockRepo.return_value
        repo_instance.archive_messages = AsyncMock(return_value=True)
        repo_instance.append_message = AsyncMock(return_value=True)

        long_history = _make_history(50)  # simulates a 50+ turn conversation
        result_history = await consolidator.preflight(
            sys_prompt="You are Chad.",
            tools=[],
            history=long_history,
            session_id="long-convo-session",
            adapter=adapter,
            langgraph_engine=langgraph_engine,
        )

    # Compaction must have run (archived old messages, appended a wake summary).
    consolidation_manager.perform_consolidation_with_safeguards.assert_awaited_once()
    repo_instance.archive_messages.assert_awaited_once()
    repo_instance.append_message.assert_awaited_once()

    # The checkpoint must be reset for this thread -- this is what keeps checkpoint
    # size bounded across an arbitrarily long conversation instead of growing forever.
    langgraph_engine.clear_checkpoints.assert_awaited_once_with("long-convo-session")

    # preflight returns the reloaded (post-compaction, much shorter) history.
    assert result_history == _make_history(2)
    assert len(result_history) < len(long_history)


@pytest.mark.asyncio
async def test_preflight_skips_compaction_when_under_threshold():
    consolidation_manager = MagicMock()
    consolidation_manager.limits = {"hard": 150000}  # generous cap, short history won't cross it
    consolidation_manager.perform_consolidation_with_safeguards = AsyncMock()

    history_loader = MagicMock()
    history_loader.load = AsyncMock()

    langgraph_engine = MagicMock()
    langgraph_engine.clear_checkpoints = AsyncMock()

    consolidator = TurnConsolidator(
        consolidation_manager=consolidation_manager,
        event_bus=None,
        history_loader=history_loader,
    )

    short_history = _make_history(2, content_repeat=5)
    result_history = await consolidator.preflight(
        sys_prompt="You are Chad.",
        tools=[],
        history=short_history,
        session_id="short-convo-session",
        adapter=MagicMock(),
        langgraph_engine=langgraph_engine,
    )

    consolidation_manager.perform_consolidation_with_safeguards.assert_not_awaited()
    langgraph_engine.clear_checkpoints.assert_not_awaited()
    assert result_history == short_history
