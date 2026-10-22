import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.core.intelligence.entity_grounding_client import EntityGroundingClient
from src.core.orchestration.nodes.graphrag_node import fetch_knowledge_boost, format_knowledge_boost


@pytest.fixture
def temp_cache_file(tmp_path: Path) -> Path:
    cache_file = tmp_path / "test_grounded_entities.json"
    return cache_file


class TestEntityGroundingClient:
    """Test suite for EntityGroundingClient and knowledge boost integration."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_grounded_context_instantly(self, temp_cache_file: Path):
        client = EntityGroundingClient(cache_path=temp_cache_file)
        
        # "gemini notebook" is pre-seeded in the cache
        result = await client.search("go to my gemini notebook for wireless networks")
        assert result is not None
        assert "Google NotebookLM" in result
        assert "notebooklm.google.com" in result
        assert "NOT a local file or Google Drive document" in result

    @pytest.mark.asyncio
    async def test_cache_miss_executes_live_search(self, temp_cache_file: Path):
        mock_registry = MagicMock()
        mock_registry.execute_tool = AsyncMock(return_value={
            "success": True,
            "results": [
                {
                    "query": "acme app official website app",
                    "sources": [
                        {
                            "title": "Acme SaaS",
                            "url": "https://acme.io/dashboard",
                            "snippet": "Acme project management web application.",
                        }
                    ],
                }
            ],
        })

        client = EntityGroundingClient(tool_registry=mock_registry, cache_path=temp_cache_file)
        result = await client.search("navigate to acme app and check the backlog")

        assert result is not None
        assert "Acme SaaS" in result
        assert "https://acme.io/dashboard" in result
        assert "NOT a local file" in result
        mock_registry.execute_tool.assert_called_once()

        # Verify it was saved to cache
        cached = client._cache.get("acme app")
        assert cached is not None
        assert cached["url"] == "https://acme.io/dashboard"

    @pytest.mark.asyncio
    async def test_negative_query_skips_search_and_returns_none(self, temp_cache_file: Path):
        mock_registry = MagicMock()
        mock_registry.execute_tool = AsyncMock()

        client = EntityGroundingClient(tool_registry=mock_registry, cache_path=temp_cache_file)
        result = await client.search("organize my downloads folder by file type")

        assert result is None
        mock_registry.execute_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_parallel_fetch_knowledge_boost_integration(self, temp_cache_file: Path):
        mock_graph = MagicMock()
        mock_graph.search = AsyncMock(return_value=None)
        
        mock_drive = MagicMock()
        mock_drive.search = AsyncMock(return_value="- Drive folder: Canvas_Resources (folder)")

        entity_client = EntityGroundingClient(cache_path=temp_cache_file)

        boost = await fetch_knowledge_boost(
            query="go to my gemini notebook for wireless networks",
            graph_client=mock_graph,
            drive_client=mock_drive,
            entity_client=entity_client,
        )

        assert "### 🌐 GROUNDED WEB SERVICES & APPS" in boost
        assert "Google NotebookLM" in boost
        assert "### DOCUMENT CONTEXT (DRIVE)" in boost
