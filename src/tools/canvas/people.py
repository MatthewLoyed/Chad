"""
Canvas People Tools
Tools for listing people in a course.
"""

from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.tools.canvas.manager import CanvasManager, logger

class GetCoursePeopleTool(BaseTool):
    """Tool to list people (students, instructors) in a Canvas course."""
    
    name = "get_course_people"
    description = "List all people enrolled in a Canvas course - students, instructors, TAs. Can search by name."
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
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
                        "description": "Canvas course ID."
                    },
                    "search_term": {
                        "type": "string",
                        "description": "Optional search term to filter by name."
                    },
                    "enrollment_type": {
                        "type": "string",
                        "description": "Optional filter: 'student', 'teacher', 'ta', 'observer', 'designer'."
                    }
                },
                "required": ["course_id"]
            }
        }
    
    async def execute(self, course_id: int, search_term: str = None, enrollment_type: str = None, **kwargs) -> Dict[str, Any]:
        try:
            params = {
                "per_page": 50,
                # "include[]": ["enrollments", "avatar_url"] # Optional inclusions
            }
            
            if search_term:
                params["search_term"] = search_term
            
            if enrollment_type:
                params["enrollment_type[]"] = enrollment_type
            
            result = self._manager.get(
                f"/courses/{course_id}/users",
                params=params,
                paginate=True
            )
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            users = result["data"]
            
            if not users:
                match_suffix = f' matching "{search_term}"' if search_term else ''
                return {
                    "success": True,
                    "result": f"No users found{match_suffix}.",
                    "users": []
                }
            
            formatted_users = []
            for user in users:
                formatted_users.append({
                    "id": user.get("id"),
                    "name": user.get("name"),
                    "sortable_name": user.get("sortable_name", ""),
                    "short_name": user.get("short_name", "")
                })
            
            return {
                "success": True,
                "result": f"Found {len(formatted_users)} users.",
                "users": formatted_users
            }
            
        except Exception as e:
            logger.error(f"Failed to get course people: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
