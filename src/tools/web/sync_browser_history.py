"""
Sync Browser History Tool - Trigger a manual sync of Chrome history.
"""

from typing import Dict, Any, Optional
import asyncio
from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display
from src.core.intelligence.memory_engine import MemoryEngine
from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager
from src.core.intelligence.ingestion.chrome import ChromeHistoryIngestor
from src.shared.logger import setup_logger

logger = setup_logger("SyncBrowserHistoryTool")

@tool_display("Syncing browser history from Chrome...")
class SyncBrowserHistoryTool(BaseTool):
    """Tool for triggering a manual synchronization of the user's Chrome browser history."""

    name = "refresh_browser_history"
    description = (
        "COMMAND: Manually refresh and sync the user's latest browser/Chrome history into Chad's memory. "
        "Use this if the user asks you to 'check my latest history' or if you suspect your history data is stale. "
        "This process extracts recent URLs and YouTube videos and performs vector embedding for search."
    )
    category = "web"

    def __init__(self, config: Optional[Dict[str, Any]] = None, memory_engine: Optional[MemoryEngine] = None):
        super().__init__(config=config)
        # Accepts an injected engine; the default is a session-agnostic MemoryEngine
        # because browser history lives in the shared personal_index (refs #246).
        self.memory = memory_engine if memory_engine else MemoryEngine()

    def get_schema(self) -> Dict[str, Any]:
        """Return the function schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "The history source to sync. Currently only 'chrome' is supported.",
                        "default": "chrome",
                        "enum": ["chrome"]
                    }
                }
            }
        }

    async def execute(self, source: str = "chrome", **kwargs) -> Dict[str, Any]:
        try:
            # We instantiate a temporary SyncManager with just the Chrome ingestor
            manager = IngestionSyncManager(self.memory)
            manager.register_ingestor(ChromeHistoryIngestor())
            
            # Run the sync cycle in a thread to avoid blocking the event loop
            report = await asyncio.to_thread(manager.run_sync_cycle)
            
            chrome_report = report.get("sources", {}).get("chrome", {})
            status = chrome_report.get("status", "unknown")
            added = chrome_report.get("added", 0)
            
            if status == "success":
                return {
                    "success": True,
                    "added_count": added,
                    "message": f"Successfully synced {added} new items from Chrome history."
                }
            elif status == "partial":
                return {
                    "success": True,
                    "added_count": added,
                    "message": f"Partially synced {added} items. Sync was interrupted (likely rate-limited)."
                }
            else:
                error = chrome_report.get("error", "Unknown error during sync.")
                return {"success": False, "error": error}
                
        except Exception as e:
            logger.error(f"Error in refresh_browser_history: {e}")
            return {"success": False, "error": str(e)}
