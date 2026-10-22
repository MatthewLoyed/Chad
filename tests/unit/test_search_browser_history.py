import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import asyncio

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.memory_engine import MemoryEngine
from src.tools.web.search_browser_history import SearchBrowserHistoryTool


class TestSearchBrowserHistory(unittest.TestCase):
    def setUp(self):
        # Create a mock repository and supabase client
        self.mock_repo = MagicMock()
        self.mock_supabase = MagicMock()
        self.mock_repo.supabase = self.mock_supabase
        self.mock_repo.mem_table = "memories"
        
        # Patch the initialization of MemoryEngine's dependency
        with patch('src.infrastructure.repositories.memory_repository.MemoryRepository') as mock_repo_class:
            mock_repo_class.return_value = self.mock_repo
            self.memory_engine = MemoryEngine()
            
        self.memory_engine.repository = self.mock_repo
        
    def test_map_and_dedup_history(self):
        items = [
            {"content": "Watched YouTube Video: 'Retro Gaming' (https://youtube.com/watch?v=1)", "created_at": "2026-05-18T00:00:00Z", "similarity": 0.95},
            {"content": "Watched YouTube Video: 'Retro Gaming' (https://youtube.com/watch?v=1)", "created_at": "2026-05-18T00:01:00Z", "similarity": 0.96}, # Duplicate
            {"content": "Visited Web Page: 'Gemini AI' (https://google.com)", "created_at": "2026-05-18T00:02:00Z", "similarity": 0.85},
            {"content": "", "created_at": "2026-05-18T00:03:00Z", "similarity": 0.50} # Empty content
        ]
        
        deduped = self.memory_engine._map_and_dedup_history(items, limit=10)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["content"], "Watched YouTube Video: 'Retro Gaming' (https://youtube.com/watch?v=1)")
        self.assertEqual(deduped[0]["timestamp"], "2026-05-18T00:00:00Z")
        self.assertEqual(deduped[0]["similarity"], 0.95)
        
        self.assertEqual(deduped[1]["content"], "Visited Web Page: 'Gemini AI' (https://google.com)")
        self.assertEqual(deduped[1]["timestamp"], "2026-05-18T00:02:00Z")
        self.assertEqual(deduped[1]["similarity"], 0.85)

    def test_search_browser_history_relevance(self):
        # Setup mock embeddings
        self.memory_engine.get_embedding = MagicMock(return_value=[0.1, 0.2, 0.3])
        
        mock_results = [
            {"content": "Visited Web Page: 'Target A' (url1)", "category": "personal_index", "created_at": "2026-05-18T00:00:00Z", "similarity": 0.92},
            {"content": "Watched YouTube Video: 'Target B' (url2)", "category": "personal_index", "created_at": "2026-05-18T00:02:00Z", "similarity": 0.95},
            {"content": "Preference fact (General memory)", "category": "general", "created_at": "2026-05-18T00:01:00Z", "similarity": 0.99} # Filtered out
        ]
        
        self.mock_repo.vector_search_memories = MagicMock(return_value=mock_results)
        
        results = self.memory_engine.search_browser_history("test", limit=5, sort_by="relevance")
        
        # Should be filtered to personal_index and sorted by similarity descending
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["content"], "Watched YouTube Video: 'Target B' (url2)") # higher similarity (0.95)
        self.assertEqual(results[1]["content"], "Visited Web Page: 'Target A' (url1)") # lower similarity (0.92)

    def test_search_browser_history_recency(self):
        # Setup mock embeddings
        self.memory_engine.get_embedding = MagicMock(return_value=[0.1, 0.2, 0.3])
        
        mock_results = [
            {"content": "Visited Web Page: 'Target A' (url1)", "category": "personal_index", "created_at": "2026-05-18T00:00:00Z", "similarity": 0.92},
            {"content": "Watched YouTube Video: 'Target B' (url2)", "category": "personal_index", "created_at": "2026-05-18T00:02:00Z", "similarity": 0.85}
        ]
        
        self.mock_repo.vector_search_memories = MagicMock(return_value=mock_results)
        
        results = self.memory_engine.search_browser_history("test", limit=5, sort_by="recency")
        
        # Sorted by created_at descending
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["content"], "Watched YouTube Video: 'Target B' (url2)") # newer (00:02:00Z)
        self.assertEqual(results[1]["content"], "Visited Web Page: 'Target A' (url1)") # older (00:00:00Z)

    def test_search_browser_history_keyword_fallback(self):
        # Embedding unavailable
        self.memory_engine.get_embedding = MagicMock(return_value=None)
        
        mock_results = [
            {"content": "Visited Web Page: 'Fallback A' (url1)", "category": "personal_index", "created_at": "2026-05-18T00:00:00Z"},
            {"content": "Watched YouTube Video: 'Fallback B' (url2)", "category": "personal_index", "created_at": "2026-05-18T00:02:00Z"}
        ]
        
        # Keyword fallback now routes through the repository's category-search method
        # (backend-agnostic) rather than reaching into repo.supabase.query directly.
        self.mock_repo.search_memories_by_category = MagicMock(return_value=mock_results)

        results = self.memory_engine.search_browser_history("test", limit=5, sort_by="recency")

        # Sorted by recency
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["content"], "Watched YouTube Video: 'Fallback B' (url2)")
        self.assertEqual(results[1]["content"], "Visited Web Page: 'Fallback A' (url1)")
        self.mock_repo.search_memories_by_category.assert_called_once()
        _, kwargs = self.mock_repo.search_memories_by_category.call_args
        self.assertEqual(kwargs["category"], "personal_index")

    def test_tool_execute(self):
        mock_memory = MagicMock()
        mock_memory.search_browser_history = MagicMock(return_value=[
            {"content": "Watched YouTube Video: 'Game' (url)", "timestamp": "2026-05-18", "similarity": 0.98}
        ])
        
        tool = SearchBrowserHistoryTool(memory_engine=mock_memory)
        
        # Execute the tool and check the output
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(tool.execute("query", limit=5, sort_by="recency"))
            self.assertTrue(res["success"])
            self.assertEqual(res["count"], 1)
            self.assertEqual(res["results"][0]["content"], "Watched YouTube Video: 'Game' (url)")
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
