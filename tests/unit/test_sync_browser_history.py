import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import asyncio

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tools.web.sync_browser_history import SyncBrowserHistoryTool

class TestSyncBrowserHistory(unittest.TestCase):
    def setUp(self):
        self.mock_memory = MagicMock()
        self.tool = SyncBrowserHistoryTool(memory_engine=self.mock_memory)

    @patch('src.tools.web.sync_browser_history.IngestionSyncManager')
    @patch('src.tools.web.sync_browser_history.ChromeHistoryIngestor')
    def test_sync_success(self, mock_ingestor_class, mock_manager_class):
        # Setup mocks
        mock_manager = mock_manager_class.return_value
        mock_manager.run_sync_cycle.return_value = {
            "sources": {
                "chrome": {
                    "status": "success",
                    "added": 42
                }
            }
        }
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(self.tool.execute(source="chrome"))
            self.assertTrue(res["success"])
            self.assertEqual(res["added_count"], 42)
            self.assertIn("Successfully synced 42 new items", res["message"])
        finally:
            loop.close()

    @patch('src.tools.web.sync_browser_history.IngestionSyncManager')
    @patch('src.tools.web.sync_browser_history.ChromeHistoryIngestor')
    def test_sync_partial(self, mock_ingestor_class, mock_manager_class):
        # Setup mocks
        mock_manager = mock_manager_class.return_value
        mock_manager.run_sync_cycle.return_value = {
            "sources": {
                "chrome": {
                    "status": "partial",
                    "added": 10
                }
            }
        }
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(self.tool.execute(source="chrome"))
            self.assertTrue(res["success"])
            self.assertEqual(res["added_count"], 10)
            self.assertIn("Partially synced 10 items", res["message"])
        finally:
            loop.close()

    @patch('src.tools.web.sync_browser_history.IngestionSyncManager')
    @patch('src.tools.web.sync_browser_history.ChromeHistoryIngestor')
    def test_sync_failure(self, mock_ingestor_class, mock_manager_class):
        # Setup mocks
        mock_manager = mock_manager_class.return_value
        mock_manager.run_sync_cycle.return_value = {
            "sources": {
                "chrome": {
                    "status": "failed",
                    "error": "Database locked"
                }
            }
        }
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(self.tool.execute(source="chrome"))
            self.assertFalse(res["success"])
            self.assertEqual(res["error"], "Database locked")
        finally:
            loop.close()

if __name__ == "__main__":
    unittest.main()
