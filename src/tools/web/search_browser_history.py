"""
Search Browser History Tool - Search Synced Chrome & YouTube Web History
"""

from typing import Dict, Any, Optional, List
import asyncio
from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display
from src.core.intelligence.memory_engine import MemoryEngine
from src.shared.logger import setup_logger

logger = setup_logger("SearchBrowserHistoryTool")

@tool_display("Searching browser history for '{query}'...")
class SearchBrowserHistoryTool(BaseTool):
    """Tool for searching synced Chrome web history and YouTube video visits."""

    name = "search_browser_history"
    description = (
        "COMMAND: Search the user's browser/Chrome history (including watched YouTube videos, "
        "visited GitHub repos, web pages, and Google searches) synced from their local profile. "
        "Supports sorting by 'relevance' (best topical matches) or 'recency' (most recent matching events)."
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
                    "query": {
                        "type": "string",
                        "description": "The search query (e.g., 'gemini python library', 'retro gaming', or 'cooking')."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of history items to return (default is 10).",
                        "default": 10
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "Order of results. Use 'relevance' for closest matches, or 'recency' for most recent matches.",
                        "enum": ["relevance", "recency"],
                        "default": "relevance"
                    }
                },
                "required": ["query"]
            }
        }

    async def execute(self, query: str, limit: int = 10, sort_by: str = "relevance", **kwargs) -> Dict[str, Any]:
        try:
            results = await asyncio.to_thread(
                self.memory.search_browser_history,
                query=query,
                limit=limit,
                sort_by=sort_by
            )
            return {
                "success": True,
                "count": len(results),
                "results": results,
                "caption": f"Found {len(results)} history items matching '{query}' sorted by {sort_by}."
            }
        except Exception as e:
            logger.error(f"Error in search_browser_history: {e}")
            return {"success": False, "error": str(e)}

    def validate_params(self, query: str = None, **kwargs) -> tuple[bool, str | None]:
        if not query or not query.strip():
            return False, "Query is required for searching browser history."
        return True, None
