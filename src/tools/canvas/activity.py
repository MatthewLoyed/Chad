"""
Canvas Activity Stream Tools
Tools for reading recent activity, notifications, announcements, and grade changes across all courses.
"""

import re
from datetime import datetime
from typing import Dict, Any, Optional, List
from src.tools.base_tool import BaseTool
from src.tools.canvas.manager import CanvasManager, logger


def format_canvas_date(iso_date: Optional[str]) -> str:
    """Format Canvas ISO-8601 date string to friendly readable format."""
    if not iso_date:
        return "No date"
    try:
        dt = datetime.fromisoformat(str(iso_date).replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except Exception:
        return str(iso_date)


def strip_html(html_content: Optional[str]) -> str:
    """Clean HTML tags and formatting."""
    if not html_content:
        return ""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?(p|div|br|li|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()


class GetCanvasActivityStreamTool(BaseTool):
    """Tool to fetch the user's recent Canvas activity stream across all courses."""
    
    name = "get_canvas_activity_stream"
    description = (
        "Get the user's global Canvas activity feed (recent announcements, grade updates, discussion replies, "
        "and submission comments across all courses)."
    )
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        return "Fetching Canvas activity stream"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = CanvasManager()
        
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 15,
                        "description": "Number of recent activity stream items to retrieve (default: 15)."
                    }
                }
            }
        }
        
    async def execute(self, limit: int = 15, **kwargs) -> Dict[str, Any]:
        try:
            result = self._manager.get("/users/self/activity_stream", params={"per_page": min(limit, 30)})
            if not result["success"]:
                return {"success": False, "error": result["error"]}
                
            items = result["data"]
            if not items:
                return {"success": True, "result": "No recent activity items found.", "activities": []}
                
            formatted = []
            for it in items[:limit]:
                formatted.append({
                    "id": it.get("id"),
                    "type": it.get("type"),  # Announcement, Conversation, DiscussionTopic, Submission, etc.
                    "title": it.get("title", "Activity item"),
                    "course_id": it.get("course_id"),
                    "created_at": format_canvas_date(it.get("created_at")),
                    "author": (it.get("author") or {}).get("display_name") or it.get("author_name"),
                    "message_preview": strip_html(it.get("message", ""))[:250] if it.get("message") else None,
                    "html_url": it.get("html_url")
                })
                
            return {
                "success": True,
                "result": f"Found {len(formatted)} recent activity items.",
                "activities": formatted
            }
        except Exception as e:
            logger.error(f"Failed to get activity stream: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
