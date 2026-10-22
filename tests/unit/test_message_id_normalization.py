"""A client-supplied message id must never be able to drop the message.

`message_id` is written straight into the messages table's PRIMARY KEY. Supabase types
that column `uuid`; local sqlite types it TEXT. The web client generated turn ids as
`Math.random().toString(36).slice(2,10)` twice over — 16 base36 chars — so every assistant
reply from the PWA made Postgres 400 the INSERT.

The user message is written WITHOUT an id (the DB generates one), so it always landed.
Result: PWA chats reloaded containing the user's questions and none of Chad's answers,
while local sqlite dev accepted the same ids happily and showed nothing wrong.

Observed live before the fix:
    ERROR:SupabaseManager:Supabase insert failed on messages: '400 Bad Request'
    ERROR:ChatHistoryRepository:Failed to append message to conversation d93d6453-...
"""

import uuid

import pytest

from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository


_safe = ChatHistoryRepository._safe_message_id


def test_a_real_uuid_is_preserved():
    """The desktop sends str(uuid4()); correlation between the streamed bubble and the
    stored row depends on it surviving untouched."""
    mid = str(uuid.uuid4())
    assert _safe(mid) == mid


def test_the_web_clients_old_turn_id_is_dropped_not_stored():
    """`uid() + uid()` — the exact shape that 400'd every assistant reply."""
    assert _safe("k3j9dl2xq8fz1a4b") is None


@pytest.mark.parametrize("bad", [
    "turn_1786328800",       # prefixed counter
    "not-a-uuid",
    "12345",
    "",                       # empty string is not an id
    "d93d6453-0eb2-4ee7",    # truncated uuid
])
def test_unusable_ids_are_dropped(bad):
    assert _safe(bad) is None


def test_none_stays_none():
    """User messages pass no id at all — the DB generates one."""
    assert _safe(None) is None


def test_uppercase_and_braced_uuids_are_accepted():
    """uuid.UUID() parses these, and Postgres accepts them, so don't throw the id away."""
    plain = uuid.uuid4()
    assert _safe(str(plain).upper()) is not None
    assert _safe("{" + str(plain) + "}") is not None


def test_a_non_string_id_does_not_raise():
    """Defensive: a malformed payload must not take down the write path."""
    assert _safe(12345) is None or isinstance(_safe(12345), str)


# --- wiring: the helper is worthless unless append_message actually calls it ---


class _RecordingBackend:
    def __init__(self):
        self.seen = []

    async def append_message(self, cleaned_id, role, content, metadata, message_id):
        self.seen.append(message_id)
        return {"id": message_id or "generated"}


def _repo_with(backend) -> ChatHistoryRepository:
    repo = ChatHistoryRepository.__new__(ChatHistoryRepository)
    repo._backend = backend
    return repo


def test_append_message_strips_an_unusable_id_before_it_reaches_the_backend():
    import asyncio

    backend = _RecordingBackend()
    repo = _repo_with(backend)
    conv = str(uuid.uuid4())

    asyncio.run(repo.append_message(conv, "assistant", "the answer", message_id="k3j9dl2xq8fz1a4b"))

    assert backend.seen == [None], "the 400-inducing id must not reach the backend"


def test_append_message_passes_a_valid_uuid_through():
    import asyncio

    backend = _RecordingBackend()
    repo = _repo_with(backend)
    conv, mid = str(uuid.uuid4()), str(uuid.uuid4())

    asyncio.run(repo.append_message(conv, "assistant", "the answer", message_id=mid))

    assert backend.seen == [mid]
