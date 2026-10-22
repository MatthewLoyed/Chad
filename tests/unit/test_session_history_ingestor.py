"""Unit tests for SessionHistoryIngestor — turn-pairing logic and checkpoint extraction."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage

from src.core.intelligence.ingestion.session_history import SessionHistoryIngestor


def test_get_source_name():
    assert SessionHistoryIngestor().get_source_name() == "session_history"


def test_ingest_returns_empty_when_db_missing(tmp_path):
    ingestor = SessionHistoryIngestor(checkpoint_db_path=str(tmp_path / "does_not_exist.db"))
    assert ingestor.ingest() == []


# ---------------------------------------------------------------------------
# _stringify — flattens langchain message content to plain text
# ---------------------------------------------------------------------------

def test_stringify_plain_string():
    assert SessionHistoryIngestor._stringify("hello world") == "hello world"


def test_stringify_content_block_list():
    content = [{"type": "text", "text": "part one"}, {"type": "text", "text": "part two"}]
    assert SessionHistoryIngestor._stringify(content) == "part one part two"


def test_stringify_ignores_non_text_blocks():
    content = [{"type": "image", "url": "x"}, {"type": "text", "text": "caption"}]
    assert SessionHistoryIngestor._stringify(content) == "caption"


def test_stringify_empty_or_none():
    assert SessionHistoryIngestor._stringify(None) == ""
    assert SessionHistoryIngestor._stringify([]) == ""


# ---------------------------------------------------------------------------
# _extract_turns — pairs each human turn with the assistant reply that follows
# ---------------------------------------------------------------------------

def test_extract_turns_pairs_human_and_ai():
    messages = [
        HumanMessage(content="What's the deal with session memory?"),
        AIMessage(content="It should index the checkpoint store."),
    ]
    turns = SessionHistoryIngestor()._extract_turns(messages)
    assert turns == [(0, "What's the deal with session memory?", "It should index the checkpoint store.")]


def test_extract_turns_skips_empty_tool_call_only_ai_message():
    """A tool-call-only AIMessage (empty content) must not count as the reply —
    the ingestor should keep walking to the next AIMessage that has real text."""
    messages = [
        HumanMessage(content="search for anime"),
        AIMessage(content="", tool_calls=[{"name": "web_search", "args": {}, "id": "1"}]),
        AIMessage(content="Found a few anime streaming sites."),
    ]
    turns = SessionHistoryIngestor()._extract_turns(messages)
    assert len(turns) == 1
    assert turns[0][1] == "search for anime"
    assert turns[0][2] == "Found a few anime streaming sites."


def test_extract_turns_skips_turn_with_no_assistant_reply():
    """A human turn with no eventual assistant text (e.g. session cut off mid-tool-loop)
    should be dropped, not emitted as a half-empty exchange."""
    messages = [
        HumanMessage(content="do the thing"),
        AIMessage(content="", tool_calls=[{"name": "noop", "args": {}, "id": "1"}]),
    ]
    turns = SessionHistoryIngestor()._extract_turns(messages)
    assert turns == []


def test_extract_turns_handles_multiple_exchanges():
    messages = [
        HumanMessage(content="first question"),
        AIMessage(content="first answer"),
        HumanMessage(content="second question"),
        AIMessage(content="second answer"),
    ]
    turns = SessionHistoryIngestor()._extract_turns(messages)
    assert len(turns) == 2
    assert turns[0][1:] == ("first question", "first answer")
    assert turns[1][1:] == ("second question", "second answer")


def test_extract_turns_concatenates_multiple_ai_messages_before_next_human():
    messages = [
        HumanMessage(content="plan a trip"),
        AIMessage(content="Step one: pick a destination."),
        AIMessage(content="Step two: book flights."),
    ]
    turns = SessionHistoryIngestor()._extract_turns(messages)
    assert len(turns) == 1
    assert "Step one" in turns[0][2] and "Step two" in turns[0][2]


# ---------------------------------------------------------------------------
# ingest() — end-to-end against a real local sqlite checkpoint store
#
# ingest() is a *sync* method that internally drives its own event loop via
# asyncio.run() (it's called from a plain worker thread in production via
# run_in_executor, with no loop already running). These tests must stay sync
# for the same reason — calling it from inside an async test would nest
# asyncio.run() inside a running loop and raise.
# ---------------------------------------------------------------------------

async def _write_checkpoint(db_path: str, thread_id: str, ts: str, messages: list) -> None:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with aiosqlite.connect(db_path) as conn:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        checkpoint = {
            "v": 1,
            "id": str(uuid.uuid4()),
            "ts": ts,
            "channel_values": {"messages": messages},
            "channel_versions": {},
            "versions_seen": {},
        }
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        await saver.aput(config, checkpoint, {}, {})


def test_ingest_extracts_exchanges_from_real_checkpoint(tmp_path):
    import asyncio

    db_path = str(tmp_path / "checkpoints.db")
    asyncio.run(_write_checkpoint(
        db_path,
        thread_id="thread-1",
        ts="2026-07-20T10:00:00+00:00",
        messages=[
            HumanMessage(content="search anime website"),
            AIMessage(content="Here's a few options."),
        ],
    ))

    ingestor = SessionHistoryIngestor(checkpoint_db_path=db_path)
    items = ingestor.ingest()

    assert len(items) == 1
    item = items[0]
    assert "search anime website" in item["content"]
    assert "Here's a few options." in item["content"]
    assert item["metadata"]["source"] == "session_history"
    assert item["metadata"]["thread_id"] == "thread-1"
    assert item["metadata"]["hash"]
    assert 0.0 < item["importance"] <= 1.0


def test_ingest_respects_last_sync_watermark(tmp_path):
    import asyncio

    db_path = str(tmp_path / "checkpoints.db")
    asyncio.run(_write_checkpoint(
        db_path,
        thread_id="old-thread",
        ts="2026-01-01T00:00:00+00:00",
        messages=[HumanMessage(content="old question"), AIMessage(content="old answer")],
    ))
    asyncio.run(_write_checkpoint(
        db_path,
        thread_id="new-thread",
        ts="2026-07-20T00:00:00+00:00",
        messages=[HumanMessage(content="new question"), AIMessage(content="new answer")],
    ))

    ingestor = SessionHistoryIngestor(checkpoint_db_path=db_path)
    last_sync = datetime(2026, 3, 1, tzinfo=timezone.utc)
    items = ingestor.ingest(last_sync=last_sync)

    assert len(items) == 1
    assert items[0]["metadata"]["thread_id"] == "new-thread"


def test_ingest_ignores_threads_with_no_messages(tmp_path):
    import asyncio

    db_path = str(tmp_path / "checkpoints.db")
    asyncio.run(_write_checkpoint(db_path, thread_id="empty-thread", ts="2026-07-20T00:00:00+00:00", messages=[]))

    ingestor = SessionHistoryIngestor(checkpoint_db_path=db_path)
    assert ingestor.ingest() == []
