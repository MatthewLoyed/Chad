"""
Canvas Calendar Tools
Tools for fetching events, office hours, review sessions, and calendar schedules.
"""

from datetime import datetime, timedelta, timezone
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


class GetCanvasCalendarEventsTool(BaseTool):
    """Tool to query events and schedule items from the Canvas calendar."""
    
    name = "get_canvas_calendar_events"
    description = (
        "Fetch upcoming calendar events, lecture review sessions, office hours, and deadlines from the Canvas calendar. "
        "Supports filtering by date range and course."
    )
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        cid = params.get("course_id")
        return f"Fetching Canvas calendar events for course {cid}" if cid else "Fetching Canvas calendar events"
    
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
                    "course_id": {
                        "type": "integer",
                        "description": "Optional: Canvas course ID to filter events for a specific class."
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Optional start date in YYYY-MM-DD format (defaults to today)."
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Optional end date in YYYY-MM-DD format (defaults to 30 days from start date)."
                    },
                    "type": {
                        "type": "string",
                        "enum": ["event", "assignment"],
                        "default": "event",
                        "description": "Event type: 'event' for calendar appointments/office hours, 'assignment' for due dates."
                    }
                }
            }
        }
        
    async def execute(
        self,
        course_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        type: str = "event",
        **kwargs
    ) -> Dict[str, Any]:
        try:
            now = datetime.now(timezone.utc)
            start_str = start_date if start_date else now.strftime("%Y-%m-%d")
            end_str = end_date if end_date else (now + timedelta(days=30)).strftime("%Y-%m-%d")
            
            params = {
                "type": type,
                "start_date": start_str,
                "end_date": end_str,
                "per_page": 50
            }
            if course_id:
                params["context_codes[]"] = f"course_{course_id}"
                
            result = self._manager.get("/calendar_events", params=params, paginate=True)
            if not result["success"]:
                return {"success": False, "error": result["error"]}
                
            events = result["data"]
            if not events:
                return {
                    "success": True,
                    "result": f"No calendar events found between {start_str} and {end_str}.",
                    "events": []
                }
                
            formatted = []
            for ev in events[:40]:
                formatted.append({
                    "id": ev.get("id"),
                    "title": ev.get("title", "Untitled Event"),
                    "start_at": format_canvas_date(ev.get("start_at")),
                    "end_at": format_canvas_date(ev.get("end_at")),
                    "all_day": ev.get("all_day", False),
                    "location_name": ev.get("location_name"),
                    "location_address": ev.get("location_address"),
                    "context_name": ev.get("context_name"),
                    "description": ev.get("description", "")[:250] if ev.get("description") else None,
                    "html_url": ev.get("html_url")
                })
                
            return {
                "success": True,
                "result": f"Found {len(events)} calendar events between {start_str} and {end_str}.",
                "events": formatted
            }
        except Exception as e:
            logger.error(f"Failed to get calendar events: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
