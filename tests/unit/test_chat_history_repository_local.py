"""Characterization tests for ChatHistoryRepository's local (sqlite) backend.

Lock the observable behavior of the zero-config local path (issue #416) so the
backend-strategy refactor can be proven behavior-preserving. Every method is exercised
end-to-end against a real temp sqlite DB (via CHAD_LOCAL_DB_PATH), with Supabase disabled.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A ChatHistoryRepository wired to a fresh temp sqlite DB with Supabase disabled."""
    from src.infrastructure.database import local_store

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)

    fake_supabase = MagicMock()
    fake_supabase.enabled = False
    with patch(
        "src.infrastructure.repositories.chat_history_repository.get_supabase_manager",
        return_value=fake_supabase,
    ):
        yield ChatHistoryRepository()


@pytest.mark.asyncio
async def test_create_and_get_conversation(repo):
    conv = await repo.create_conversation(model="qwen", title="First")
    assert conv["id"] and conv["title"] == "First"
    got = await repo.get_conversation(conv["id"])
    assert got is not None and got["title"] == "First"
    assert got["is_pinned"] is False


@pytest.mark.asyncio
async def test_get_conversations_pinned_first(repo):
    a = await repo.create_conversation(title="A")
    b = await repo.create_conversation(title="B")
    await repo.update_conversation_pinned(b["id"], True)
    convs = await repo.get_conversations(limit=10, offset=0)
    assert convs[0]["id"] == b["id"]  # pinned first
    assert {c["title"] for c in convs} == {"A", "B"}


@pytest.mark.asyncio
async def test_append_and_get_messages(repo):
    conv = await repo.create_conversation(title="C")
    await repo.append_message(conv["id"], "user", "hello")
    await repo.append_message(conv["id"], "assistant", "hi there")
    msgs = await repo.get_messages(conv["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_append_strips_base64_from_blocks(repo):
    conv = await repo.create_conversation(title="D")
    blocks = [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"type": "base64", "data": "AAAABBBB"}},
    ]
    await repo.append_message(conv["id"], "user", blocks)
    msgs = await repo.get_messages(conv["id"])
    # content round-trips as parsed JSON, with base64 replaced by a placeholder
    stored = msgs[0]["content"]
    assert isinstance(stored, list)
    assert stored[1]["source"]["data"] == "[image base64 omitted from history]"


@pytest.mark.asyncio
async def test_archive_and_filter(repo):
    conv = await repo.create_conversation(title="E")
    m1 = await repo.append_message(conv["id"], "user", "keep")
    m2 = await repo.append_message(conv["id"], "assistant", "archive me")
    assert await repo.archive_messages([m2["id"]]) is True
    all_msgs = await repo.get_messages(conv["id"], include_archived=True)
    active = await repo.get_messages(conv["id"], include_archived=False)
    assert len(all_msgs) == 2 and len(active) == 1
    assert active[0]["id"] == m1["id"]


@pytest.mark.asyncio
async def test_update_message_metadata_merges(repo):
    conv = await repo.create_conversation(title="F")
    m = await repo.append_message(conv["id"], "assistant", "rate me", metadata={"a": 1})
    assert await repo.update_message_metadata(m["id"], {"user_rating": "up"}) is True
    msgs = await repo.get_messages(conv["id"])
    assert msgs[0]["metadata"] == {"a": 1, "user_rating": "up"}
    assert await repo.update_message_metadata("nonexistent", {"x": 1}) is False


@pytest.mark.asyncio
async def test_update_title_pinned_project(repo):
    conv = await repo.create_conversation(title="G")
    cid = conv["id"]
    assert await repo.update_conversation_title(cid, "renamed") is True
    assert await repo.update_conversation_pinned(cid, True) is True
    assert await repo.update_conversation_project(cid, "C:/proj") is True
    got = await repo.get_conversation(cid)
    assert got["title"] == "renamed"
    assert got["is_pinned"] is True
    assert got["project_path"] == "C:/proj"


@pytest.mark.asyncio
async def test_reassign_project_path(repo):
    a = await repo.create_conversation(title="H")
    b = await repo.create_conversation(title="I")
    await repo.update_conversation_project(a["id"], "C:/old")
    await repo.update_conversation_project(b["id"], "C:/old")
    assert await repo.reassign_project_path("C:/old", "C:/new") is True
    assert (await repo.get_conversation(a["id"]))["project_path"] == "C:/new"


@pytest.mark.asyncio
async def test_delete_conversation(repo):
    conv = await repo.create_conversation(title="J")
    assert await repo.delete_conversation(conv["id"]) is True
    assert await repo.get_conversation(conv["id"]) is None


@pytest.mark.asyncio
async def test_search_messages_and_conversations(repo):
    conv = await repo.create_conversation(title="Groceries")
    await repo.append_message(conv["id"], "user", "buy oat milk")
    msg_hits = await repo.search_chat_messages("oat milk")
    assert len(msg_hits) == 1
    assert msg_hits[0]["conversation_title"] == "Groceries"
    assert "snippet" in msg_hits[0]
    assert "oat" in msg_hits[0]["snippet"].lower()
    conv_hits = await repo.search_conversations("grocer")
    assert len(conv_hits) == 1 and conv_hits[0]["title"] == "Groceries"
    assert await repo.search_chat_messages("") == []
    assert await repo.search_conversations("") == []


@pytest.mark.asyncio
async def test_search_messages_exact_url_and_snippet(repo):
    """FTS / LIKE hybrid must hit an exact URL substring (the YT-link delight case)."""
    conv = await repo.create_conversation(title="Weekend plans")
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    await repo.append_message(
        conv["id"],
        "assistant",
        f"Here's that clip you wanted: {url} — classic.",
    )
    await repo.append_message(conv["id"], "user", "unrelated shopping list: eggs bread")

    hits = await repo.search_chat_messages(url)
    assert len(hits) >= 1
    assert any(url in (h.get("snippet") or "") or url in str(h.get("content") or "") for h in hits)
    assert hits[0]["conversation_title"] == "Weekend plans"
    assert hits[0]["snippet"]  # non-empty match window


@pytest.mark.asyncio
async def test_search_messages_ranks_better_match_first(repo):
    conv_a = await repo.create_conversation(title="Tangential")
    conv_b = await repo.create_conversation(title="Golfport dinner")
    await repo.append_message(conv_a["id"], "user", "we mentioned golf once in passing")
    await repo.append_message(
        conv_b["id"],
        "user",
        "golfport dinner reservation for Saturday at 7pm with the wedding party",
    )
    hits = await repo.search_chat_messages("golfport dinner")
    assert len(hits) >= 1
    assert hits[0]["conversation_id"] == conv_b["id"]


@pytest.mark.asyncio
async def test_search_messages_special_chars_do_not_crash(repo):
    conv = await repo.create_conversation(title="Punctuation")
    await repo.append_message(conv["id"], "user", "path C:\\Users\\ExampleUser\\notes (draft)")
    # Quotes / parentheses / backslashes must not raise from FTS MATCH.
    hits = await repo.search_chat_messages('notes (draft)')
    assert isinstance(hits, list)
    hits2 = await repo.search_chat_messages('"bogus^^^query"')
    assert isinstance(hits2, list)


@pytest.mark.asyncio
async def test_truncate_conversation_after_keeps_prefix(repo):
    conv = await repo.create_conversation(title="Retry Test")
    m0 = await repo.append_message(conv["id"], "user", "turn 1 user")
    m1 = await repo.append_message(conv["id"], "assistant", "turn 1 reply")
    m2 = await repo.append_message(conv["id"], "user", "turn 2 user (retry)")
    m3 = await repo.append_message(conv["id"], "assistant", "turn 2 reply (old)")

    # Keep only first 2 messages (index 0 and 1)
    ok = await repo.truncate_conversation_after(conv["id"], keep_count=2)
    assert ok is True

    active = await repo.get_messages(conv["id"], include_archived=False)
    assert len(active) == 2
    assert [m["id"] for m in active] == [m0["id"], m1["id"]]

    all_msgs = await repo.get_messages(conv["id"], include_archived=True)
    assert len(all_msgs) == 4


@pytest.mark.asyncio
async def test_truncate_conversation_after_zero_and_excess(repo):
    conv = await repo.create_conversation(title="Retry Edge Cases")
    await repo.append_message(conv["id"], "user", "msg1")
    await repo.append_message(conv["id"], "assistant", "msg2")

    # keep_count larger than total messages: nothing archived
    assert await repo.truncate_conversation_after(conv["id"], keep_count=10) is True
    active = await repo.get_messages(conv["id"], include_archived=False)
    assert len(active) == 2

    # keep_count = 0: all archived
    assert await repo.truncate_conversation_after(conv["id"], keep_count=0) is True
    active_empty = await repo.get_messages(conv["id"], include_archived=False)
    assert len(active_empty) == 0