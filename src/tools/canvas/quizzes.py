"""
Canvas Quizzes Tools
Tools for viewing course quizzes, exams, time limits, question counts, and instructions.
"""

import re
from datetime import datetime
from typing import Dict, Any, Optional, List
from src.tools.base_tool import BaseTool
from src.tools.canvas.manager import CanvasManager, logger


def format_canvas_date(iso_date: Optional[str]) -> str:
    """Format Canvas ISO-8601 date string to friendly readable format."""
    if not iso_date:
        return "No due date"
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


class GetCourseQuizzesTool(BaseTool):
    """Tool to list quizzes and exams from a Canvas course."""
    
    name = "get_course_quizzes"
    description = (
        "List quizzes, tests, and surveys for a Canvas course. "
        "Shows time limits, question count, allowed attempts, points possible, due dates, and lock/unlock windows."
    )
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        course = params.get("course")
        return f"Listing Canvas quizzes for course '{course}'" if course else "Listing Canvas quizzes"
    
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
                        "description": "Canvas course ID. Use get_canvas_courses to find this."
                    },
                    "search_term": {
                        "type": "string",
                        "description": "Optional search term to filter quizzes by title."
                    }
                },
                "required": ["course_id"]
            }
        }
        
    async def execute(self, course_id: int, search_term: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            params = {
                "per_page": 50
            }
            if search_term:
                params["search_term"] = search_term
                
            result = self._manager.get(f"/courses/{course_id}/quizzes", params=params, paginate=True)
            if not result["success"]:
                return {"success": False, "error": result["error"]}
                
            quizzes = result["data"]
            if not quizzes:
                return {"success": True, "result": "No quizzes found for this course.", "quizzes": []}
                
            formatted = []
            for q in quizzes:
                formatted.append({
                    "id": q.get("id"),
                    "title": q.get("title", "Untitled Quiz"),
                    "quiz_type": q.get("quiz_type", "assignment"),
                    "points_possible": q.get("points_possible", 0),
                    "question_count": q.get("question_count", 0),
                    "time_limit_minutes": q.get("time_limit"),
                    "allowed_attempts": q.get("allowed_attempts", 1),
                    "due_at": format_canvas_date(q.get("due_at")),
                    "unlock_at": format_canvas_date(q.get("unlock_at")),
                    "lock_at": format_canvas_date(q.get("lock_at")),
                    "scoring_policy": q.get("scoring_policy"),
                    "has_access_code": bool(q.get("access_code")),
                    "description": strip_html(q.get("description", ""))[:300] if q.get("description") else None,
                    "html_url": q.get("html_url")
                })
                
            return {
                "success": True,
                "result": f"Found {len(formatted)} quizzes for course {course_id}.",
                "quizzes": formatted
            }
        except Exception as e:
            logger.error(f"Failed to get course quizzes: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
