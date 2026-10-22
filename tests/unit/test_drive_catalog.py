"""Unit tests for Drive catalog path helpers and injector wiring."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import HumanMessage

from src.core.intelligence.ingestion.drive_path import (
    FOLDER_MIME,
    SCOPE_MY_DRIVE,
    SCOPE_SHARED_DRIVE,
    SCOPE_SHARED_WITH_ME,
    build_node_index,
    classify_scope,
    format_catalog_content,
    is_video_mime,
    resolve_folder_path,
)
from src.core.orchestration.nodes import make_graphrag_node


def test_is_video_mime():
    assert is_video_mime("video/mp4")
    assert is_video_mime("video/quicktime")
    assert not is_video_mime("application/pdf")
    assert not is_video_mime(FOLDER_MIME)
    assert not is_video_mime("image/png")


def test_mime_and_content_format():
    content = format_catalog_content(
        name="Budget.xlsx",
        folder_path="Shared with me/Wedding/Finances",
        mime="application/vnd.google-apps.spreadsheet",
        file_id="abc123",
    )
    assert "Budget.xlsx" in content
    assert "Shared with me/Wedding/Finances" in content
    assert "spreadsheet" in content
    assert "[id=abc123]" in content


def test_catalog_compact_keeps_file_id():
    from src.core.intelligence.knowledge_context_format import compact_knowledge_item

    line = compact_knowledge_item(
        "Google Drive file 'Alex & Sam's Wedding Roadmap' at My Drive/Wedding (document)",
        metadata={"file_id": "example_document_id_000000000000000000000001"},
        max_chars=120,
    )
    assert line is not None
    assert "id=example_document_id_000000000000000000000001" in line
    assert "Sam's Wedding Roadmap" in line


def test_resolve_drive_file_id_from_url():
    from src.tools.workspace.drive import resolve_drive_file_id

    assert (
        resolve_drive_file_id(
            "https://docs.google.com/document/d/example_document_id_000000000000000000000001/edit"
        )
        == "example_document_id_000000000000000000000001"
    )
    assert resolve_drive_file_id("raw-id-here") == "raw-id-here"


def test_classify_scope_shared_drive():
    assert classify_scope({"id": "1", "driveId": "sd1"}) == SCOPE_SHARED_DRIVE


def test_classify_scope_shared_with_me_flag():
    assert (
        classify_scope({"id": "1", "sharedWithMe": True}, my_email="me@x.com")
        == SCOPE_SHARED_WITH_ME
    )


def test_classify_scope_not_owned_by_me():
    assert (
        classify_scope({"id": "1", "ownedByMe": False}, my_email="me@x.com")
        == SCOPE_SHARED_WITH_ME
    )


def test_classify_scope_owned():
    assert (
        classify_scope(
            {
                "id": "1",
                "ownedByMe": True,
                "owners": [{"emailAddress": "me@x.com", "me": True}],
            },
            my_email="me@x.com",
        )
        == SCOPE_MY_DRIVE
    )


def test_resolve_folder_path_nested():
    files = [
        {"id": "wedding", "name": "Wedding", "parents": ["root"], "mimeType": FOLDER_MIME},
        {
            "id": "fin",
            "name": "Finances",
            "parents": ["wedding"],
            "mimeType": FOLDER_MIME,
        },
        {
            "id": "budget",
            "name": "Budget.xlsx",
            "parents": ["fin"],
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "sharedWithMe": True,
        },
    ]
    nodes = build_node_index(files)
    budget = nodes["budget"]
    scope = classify_scope(budget)
    path = resolve_folder_path(budget, nodes, scope=scope)
    assert path == "Shared with me/Wedding/Finances"


def test_resolve_path_shared_drive_prefix():
    files = [
        {"id": "folder", "name": "Specs", "parents": [], "mimeType": FOLDER_MIME, "driveId": "d1"},
        {
            "id": "doc",
            "name": "plan.doc",
            "parents": ["folder"],
            "mimeType": "application/vnd.google-apps.document",
            "driveId": "d1",
        },
    ]
    nodes = build_node_index(files)
    path = resolve_folder_path(
        nodes["doc"],
        nodes,
        scope=SCOPE_SHARED_DRIVE,
        shared_drive_names={"d1": "Team Drive"},
    )
    assert path == "Team Drive/Specs"


def _make_mock_node(graph_result, activity_result=None, drive_result=None):
    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value=graph_result)

    activity_client = None
    if activity_result is not None:
        activity_client = MagicMock()
        activity_client.search = AsyncMock(return_value=activity_result)

    drive_client = None
    if drive_result is not None:
        drive_client = MagicMock()
        drive_client.search = AsyncMock(return_value=drive_result)

    return make_graphrag_node(graph_client, activity_client, drive_client)


@pytest.mark.asyncio
async def test_node_injects_drive_context():
    node = _make_mock_node(
        graph_result=None,
        activity_result=None,
        drive_result="- Google Drive file 'Budget.xlsx' at Shared with me/Wedding (spreadsheet)",
    )
    state = {
        "messages": [HumanMessage(content="where is my wedding budget spreadsheet?")],
        "system_instruction": "base",
    }
    result = await node(state, config=None)
    assert "DOCUMENT CONTEXT (DRIVE)" in result["knowledge_boost"]
    assert "Budget.xlsx" in result["knowledge_boost"]
    assert "Wedding" in result["knowledge_boost"]


@pytest.mark.asyncio
async def test_node_injects_all_three_blocks():
    node = _make_mock_node(
        graph_result="- User is planning a wedding.",
        activity_result="- Visited venue website.",
        drive_result="- Google Drive file 'Budget.xlsx' at My Drive/Wedding",
    )
    state = {
        "messages": [HumanMessage(content="what's the status of wedding planning docs?")],
        "system_instruction": "base",
    }
    result = await node(state, config=None)
    boost = result["knowledge_boost"]
    assert "RELATIONAL CONTEXT (GRAPH)" in boost
    assert "ACTIVITY CONTEXT" in boost
    assert "DOCUMENT CONTEXT (DRIVE)" in boost


@pytest.mark.asyncio
async def test_node_tolerates_drive_exception():
    graph_client = MagicMock()
    graph_client.search = AsyncMock(return_value="- The user is 30.")
    activity_client = MagicMock()
    activity_client.search = AsyncMock(return_value=None)
    drive_client = MagicMock()
    drive_client.search = AsyncMock(side_effect=RuntimeError("Drive down"))

    node = make_graphrag_node(graph_client, activity_client, drive_client)
    state = {
        "messages": [HumanMessage(content="How old am I?")],
        "system_instruction": "base",
    }
    result = await node(state, config=None)
    assert "RELATIONAL CONTEXT (GRAPH)" in result["knowledge_boost"]
    assert "DOCUMENT CONTEXT (DRIVE)" not in result["knowledge_boost"]


def test_catalog_backend_defaults_to_local(monkeypatch, tmp_path):
    """Drive catalog must prefer local sqlite even when Supabase is enabled."""
    from src.infrastructure.repositories.memory_backends import (
        LocalMemoryBackend,
        SupabaseMemoryBackend,
    )
    from src.infrastructure.repositories import memory_repository as mr_mod

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "catalog_local.db"))
    # Reset cached path so override takes effect
    import src.infrastructure.database.local_store as local_store

    local_store._DB_PATH = None

    fake_sb = MagicMock()
    fake_sb.enabled = True
    monkeypatch.setattr(mr_mod, "get_supabase_manager", lambda: fake_sb)

    def fake_get(key, default=None):
        if key == "drive_catalog.storage":
            return "local"
        if key == "memory.storage":
            return "supabase"  # cloud for general memory â€” catalog still local
        return default

    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        fake_get,
    )

    repo = mr_mod.MemoryRepository()
    assert isinstance(repo._backend, SupabaseMemoryBackend)
    assert isinstance(repo.catalog_backend, LocalMemoryBackend)


def test_memory_storage_defaults_to_local(monkeypatch, tmp_path):
    """Open-source protocol: memory.storage local even if Supabase credentials exist."""
    from src.infrastructure.repositories.memory_backends import LocalMemoryBackend
    from src.infrastructure.repositories import memory_repository as mr_mod
    import src.infrastructure.database.local_store as local_store

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "mem_local.db"))
    local_store._DB_PATH = None

    fake_sb = MagicMock()
    fake_sb.enabled = True
    monkeypatch.setattr(mr_mod, "get_supabase_manager", lambda: fake_sb)
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        lambda key, default=None: "local" if key == "memory.storage" else default,
    )

    repo = mr_mod.MemoryRepository()
    assert isinstance(repo._backend, LocalMemoryBackend)
    assert repo._memory_storage_mode() == "local"


def test_unset_storage_stays_local_when_supabase_enabled(monkeypatch, tmp_path):
    """Unset memory.storage â†’ local; memories are not a Supabase backup target."""
    from src.infrastructure.repositories.memory_backends import LocalMemoryBackend
    from src.infrastructure.repositories import memory_repository as mr_mod
    import src.infrastructure.database.local_store as local_store

    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(tmp_path / "mem_mirror.db"))
    local_store._DB_PATH = None

    fake_sb = MagicMock()
    fake_sb.enabled = True
    monkeypatch.setattr(mr_mod, "get_supabase_manager", lambda: fake_sb)
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.get",
        lambda key, default=None: default,  # storage unset
    )

    repo = mr_mod.MemoryRepository()
    assert isinstance(repo._backend, LocalMemoryBackend)
    assert repo._memory_storage_mode() == "local"
    assert isinstance(repo.catalog_backend, LocalMemoryBackend)
    assert repo._catalog_storage_mode() == "local"
