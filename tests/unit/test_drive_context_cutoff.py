# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
from types import SimpleNamespace
from unittest.mock import MagicMock
from pathlib import Path
import yaml
import pytest
from src.core.intelligence.drive_catalog_client import DriveCatalogClient
from src.core.intelligence.knowledge_context_format import format_knowledge_bullets


@pytest.mark.parametrize("embedding", [None, [1.0, 0.0]])
def test_keyword_fallback_does_not_invent_semantic_confidence(embedding):
    backend = MagicMock()
    backend.vector_search_memories.return_value = []
    backend.search_memories_by_category.return_value = [{"content": "Name-only document", "metadata": {}}]
    engine = SimpleNamespace(repository=SimpleNamespace(catalog_backend=backend), get_embedding=lambda _: embedding)
    rows = DriveCatalogClient._search_catalog(engine, "start a meeting and email its code", 5, "general")
    assert rows and rows[0]["similarity"] is None
    assert format_knowledge_bullets(rows, max_items=5, min_similarity=.42) is None


@pytest.mark.asyncio
async def test_automatic_client_rejects_live_recipient_distractors(monkeypatch):
    """Exercise search(), including its larger fetch, not an undersized raw probe."""
    from src.shared.config_manager import ConfigManager

    config = yaml.safe_load((Path(__file__).parents[2] / "config/config.yaml").read_text(encoding="utf-8"))
    settings = {"drive_catalog.relevance_threshold": config["drive_catalog"]["relevance_threshold"]}
    monkeypatch.setattr(ConfigManager, "get", lambda key, default=None: settings.get(key, default))
    backend = MagicMock()
    backend.vector_search_memories.return_value = [
        {"category": "drive_catalog", "content": title, "similarity": score, "metadata": {}}
        for title, score in [("Jordan resume", .4473893344), ("Wedding outline Jordan", .4363295734)]
    ]
    client = DriveCatalogClient()
    client._engine = SimpleNamespace(repository=SimpleNamespace(catalog_backend=backend),
                                     get_embedding=lambda _: [1.0, 0.0])
    assert await client.search("start a google meet and email jordan the code", domain=None) is None
    # Default 5 formatted items fetches 20 catalog candidates / 100 raw memories.
    assert backend.vector_search_memories.call_args.args[1] == 100
    backend.vector_search_memories.return_value = [
        {"category": "drive_catalog", "content": "Wedding service outline", "similarity": .5462, "metadata": {}}
    ]
    assert "Wedding service outline" in await client.search("find wedding service outline", domain=None)
