"""Unit tests for reflection lesson dedup (#283): queue guard + already-known context."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.lesson_gatekeeper import LessonGatekeeper
from src.core.intelligence.reflection import (
    GRAPH_DUP_THRESHOLD,
    _drop_graph_duplicates,
    _load_personal_knowledge_texts,
    dedup_corpus_from_context,
    filter_queue_duplicates,
    format_already_known_block,
    load_already_known_context,
)
from src.core.intelligence.reflection_lessons_store import (
    existing_pending_lesson_texts,
    is_duplicate_lesson,
    persist_lesson_and_publish,
)


# ── is_duplicate_lesson ───────────────────────────────────────────────────────


def test_is_duplicate_lesson_detects_near_match():
    existing = ["The user's name is Alex"]
    assert is_duplicate_lesson("The user name is Alex", existing)
    assert not is_duplicate_lesson("The user prefers dark mode", existing)


def test_is_duplicate_lesson_empty_text_returns_false():
    assert not is_duplicate_lesson("", ["something"])
    assert not is_duplicate_lesson("   ", ["something"])
    assert not is_duplicate_lesson("abc", [])  # tokens too short


def test_is_duplicate_lesson_respects_threshold():
    existing = ["The user prefers dark mode themes everywhere"]
    assert is_duplicate_lesson("User prefers dark mode themes", existing, threshold=0.5)
    assert not is_duplicate_lesson("User prefers dark mode themes", existing, threshold=0.99)


# ── gatekeeper + context formatting ───────────────────────────────────────────


def test_gatekeeper_rejects_pending_queue_duplicate():
    gk = LessonGatekeeper(
        available_tools={"web_search"},
        existing_lessons=["The user's name is Alex"],
    )
    out = gk.filter([{
        "scope": "user",
        "lesson": "The user's name is Alex",
        "confidence": 0.95,
    }])
    assert not out.admitted
    assert "duplicate of an existing" in out.rejected[0]["reason"]


def test_dedup_corpus_from_context_prefers_graph_over_personal_md():
    corpus = dedup_corpus_from_context({
        "persona_lessons": ["lesson a"],
        "queue_lessons": ["queue b"],
        "graph_facts": ["graph fact"],
        "personal_knowledge": ["pk fact"],
    })
    assert "lesson a" in corpus
    assert "queue b" in corpus
    assert "graph fact" in corpus
    assert "pk fact" not in corpus


def test_dedup_corpus_falls_back_to_personal_knowledge_when_graph_empty():
    corpus = dedup_corpus_from_context({
        "graph_facts": [],
        "personal_knowledge": ["Identity: Name is Alex"],
    })
    assert "Identity: Name is Alex" in corpus


def test_filter_queue_duplicates_drops_matching_questions():
    queue = ["What's your relationship with Sam?"]
    questions = [
        {"kind": "question", "question": "What is your relationship with Sam?", "confidence": 0.8},
        {"kind": "question", "question": "Which lamp device should I use?", "confidence": 0.7},
    ]
    kept = filter_queue_duplicates(questions, queue, field="question")
    assert len(kept) == 1
    assert "lamp" in kept[0]["question"]


def test_filter_queue_duplicates_noop_when_queue_empty():
    items = [{"question": "anything"}]
    assert filter_queue_duplicates(items, [], field="question") == items


def test_format_already_known_block_includes_sections():
    block = format_already_known_block({
        "personal_knowledge": ["Identity: Name is Alex"],
        "graph_facts": ["Alex uses Chad on Windows"],
        "persona_lessons": ["Use web_search for quick lookups"],
        "queue_lessons": ["The user's name is Alex"],
        "tools": ["web_search", "open_app"],
    })
    assert "Personal knowledge" in block
    assert "Alex" in block
    assert "Reflection review queue" in block
    assert "web_search" in block


def test_format_already_known_block_empty_context():
    assert format_already_known_block({}) == "(no canonical memory loaded)"


def test_load_personal_knowledge_missing_file_returns_empty(tmp_path, monkeypatch):
    from src.shared import paths
    monkeypatch.setattr(paths, "get_project_root", lambda: tmp_path)
    assert _load_personal_knowledge_texts() == []


# ── existing_pending_lesson_texts ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_existing_pending_lesson_texts_deduplicates():
    supabase = MagicMock()
    supabase.async_query = AsyncMock(return_value=[
        {"text": "Fact A"},
        {"text": "Fact A"},
        {"text": "  Fact B  "},
        {"text": ""},
    ])
    texts = await existing_pending_lesson_texts(supabase)
    assert texts == ["Fact A", "Fact B"]


@pytest.mark.asyncio
async def test_existing_pending_lesson_texts_query_error_returns_empty():
    supabase = MagicMock()
    supabase.async_query = AsyncMock(side_effect=RuntimeError("db down"))
    assert await existing_pending_lesson_texts(supabase) == []


@pytest.mark.asyncio
async def test_existing_pending_lesson_texts_pending_only():
    supabase = MagicMock()
    supabase.async_query = AsyncMock(return_value=[{"text": "pending row"}])
    await existing_pending_lesson_texts(supabase, include_applied=False)
    call = supabase.async_query.call_args
    assert "pending" in call.kwargs["filters"]["status"]
    assert "applied" not in call.kwargs["filters"]["status"]


# ── persist_lesson_and_publish ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_lesson_skips_queue_duplicate():
    supabase = MagicMock()
    with patch(
        "src.core.intelligence.reflection_lessons_store.existing_pending_lesson_texts",
        new=AsyncMock(return_value=["The user's name is Alex"]),
    ):
        ok = await persist_lesson_and_publish(
            supabase,
            {"scope": "user", "lesson": "The user's name is Alex", "confidence": 0.95},
            "lesson",
        )
    assert ok is False
    supabase.async_insert.assert_not_called()


@pytest.mark.asyncio
async def test_persist_lesson_rejects_empty_text():
    supabase = MagicMock()
    ok = await persist_lesson_and_publish(supabase, {"scope": "user", "lesson": "  "}, "lesson")
    assert ok is False
    supabase.async_insert.assert_not_called()


@pytest.mark.asyncio
async def test_persist_lesson_proceeds_when_dedup_check_fails():
    supabase = MagicMock()
    supabase.async_insert = AsyncMock(return_value=[{"id": "row-1"}])
    with patch(
        "src.core.intelligence.reflection_lessons_store.existing_pending_lesson_texts",
        new=AsyncMock(side_effect=RuntimeError("db timeout")),
    ):
        ok = await persist_lesson_and_publish(
            supabase,
            {"scope": "user", "lesson": "Brand new fact", "confidence": 0.9},
            "lesson",
        )
    assert ok is True
    supabase.async_insert.assert_called_once()


@pytest.mark.asyncio
async def test_persist_lesson_inserts_unique_row():
    supabase = MagicMock()
    supabase.async_insert = AsyncMock(return_value=[{"id": "row-2"}])
    with patch(
        "src.core.intelligence.reflection_lessons_store.existing_pending_lesson_texts",
        new=AsyncMock(return_value=["Unrelated existing lesson"]),
    ):
        ok = await persist_lesson_and_publish(
            supabase,
            {"scope": "tools", "lesson": "Map my lamp to bedroom device", "confidence": 0.85},
            "lesson",
        )
    assert ok is True
    row = supabase.async_insert.call_args[0][1]
    assert row["text"] == "Map my lamp to bedroom device"
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_persist_question_skips_duplicate():
    supabase = MagicMock()
    with patch(
        "src.core.intelligence.reflection_lessons_store.existing_pending_lesson_texts",
        new=AsyncMock(return_value=["Is bedroom your lamp?"]),
    ):
        ok = await persist_lesson_and_publish(
            supabase,
            {"question": "Is bedroom your lamp?", "why": "mapping", "confidence": 0.8},
            "question",
        )
    assert ok is False


# ── graph dedup ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drop_graph_duplicates_filters_known_name():
    admitted = [{"scope": "user", "lesson": "The user's name is Alex", "confidence": 0.95}]

    mock_graph = MagicMock()
    mock_graph.max_similarity = AsyncMock(return_value=(0.77, "Identity section: user Alex ..."))

    with patch("src.core.intelligence.graph_rag_client.GraphRAGClient", return_value=mock_graph):
        mock_graph.initialize = AsyncMock()
        kept, dups, active = await _drop_graph_duplicates(admitted)

    assert active is True
    assert not kept
    assert len(dups) == 1
    assert dups[0]["graph_score"] >= GRAPH_DUP_THRESHOLD


@pytest.mark.asyncio
async def test_drop_graph_duplicates_logs_when_inactive():
    admitted = [{"scope": "user", "lesson": "Some new fact", "confidence": 0.9}]

    with patch(
        "src.core.intelligence.graph_rag_client.GraphRAGClient",
        side_effect=RuntimeError("graph unavailable"),
    ):
        kept, dups, active = await _drop_graph_duplicates(admitted)

    assert active is False
    assert len(kept) == 1
    assert not dups


@pytest.mark.asyncio
async def test_drop_graph_duplicates_partial_embed_failure_keeps_lesson():
    admitted = [
        {"scope": "user", "lesson": "Fact one", "confidence": 0.9},
        {"scope": "user", "lesson": "Fact two", "confidence": 0.85},
    ]
    mock_graph = MagicMock()
    mock_graph.initialize = AsyncMock()
    mock_graph.max_similarity = AsyncMock(side_effect=[(0.3, "other"), RuntimeError("embed fail")])

    with patch("src.core.intelligence.graph_rag_client.GraphRAGClient", return_value=mock_graph):
        kept, dups, active = await _drop_graph_duplicates(admitted)

    assert active is True
    assert len(kept) == 2
    assert not dups


# ── load_already_known_context (integration-ish with mocks) ───────────────────


@pytest.mark.asyncio
async def test_load_already_known_context_survives_persona_failure():
    with patch("src.core.intelligence.reflection._load_graph_fact_texts",
               new=AsyncMock(return_value=[])), \
         patch("src.core.intelligence.reflection.fetch_available_tools", return_value=["web_search"]), \
         patch("src.core.intelligence.reflection._load_personal_knowledge_texts",
               return_value=["pk"]), \
         patch("src.infrastructure.repositories.memory_repository.MemoryRepository",
               side_effect=RuntimeError("persona down")), \
         patch("src.infrastructure.database.supabase_manager.get_supabase_manager",
               return_value=None):
        ctx = await load_already_known_context()
    assert ctx["tools"] == ["web_search"]
    assert ctx["personal_knowledge"] == ["pk"]
    assert ctx["persona_lessons"] == []


@pytest.mark.asyncio
async def test_propose_lessons_returns_filtered_questions_when_no_lessons():
    from src.core.intelligence.reflection import LessonProposal, propose_lessons

    ctx = {
        "personal_knowledge": [],
        "graph_facts": [],
        "persona_lessons": [],
        "queue_lessons": ["Is bedroom your lamp?"],
        "tools": ["web_search"],
    }
    fake_refl = MagicMock()
    fake_refl.error = ""
    fake_refl.lessons = []
    fake_refl.questions = [
        {"kind": "question", "question": "Is bedroom your lamp?", "confidence": 0.8},
        {"kind": "question", "question": "Which Spotify playlist?", "confidence": 0.7},
    ]

    with patch("src.core.intelligence.reflection.load_already_known_context",
               new=AsyncMock(return_value=ctx)), \
         patch("src.core.intelligence.reflection.reflect",
               new=AsyncMock(return_value=fake_refl)):
        proposal = await propose_lessons(5, "test-model")

    assert isinstance(proposal, LessonProposal)
    assert proposal.admitted == []
    assert len(proposal.questions) == 1
    assert "Spotify" in proposal.questions[0]["question"]
    assert proposal.available_tool_count == 1


@pytest.mark.asyncio
async def test_load_already_known_context_loads_queue_when_supabase_up():
    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    with patch("src.core.intelligence.reflection._load_graph_fact_texts",
               new=AsyncMock(return_value=[])), \
         patch("src.core.intelligence.reflection.fetch_available_tools", return_value=[]), \
         patch("src.core.intelligence.reflection._load_personal_knowledge_texts", return_value=[]), \
         patch("src.infrastructure.repositories.memory_repository.MemoryRepository") as repo_cls, \
         patch("src.infrastructure.database.supabase_manager.get_supabase_manager",
               return_value=mock_supabase), \
         patch("src.core.intelligence.reflection_lessons_store.existing_pending_lesson_texts",
               new=AsyncMock(return_value=["queued lesson"])):
        repo_cls.return_value.get_persona_config.return_value = ""
        ctx = await load_already_known_context()
    assert ctx["queue_lessons"] == ["queued lesson"]
