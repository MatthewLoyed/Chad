"""
Canvas Discussions Tools
Tools for reading course discussion boards, Q&A topics, announcements, and replies.
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


class GetCourseDiscussionsTool(BaseTool):
    """Tool to list discussion board topics from a Canvas course."""
    
    name = "get_course_discussions"
    description = (
        "List discussion board topics and forums in a Canvas course. "
        "Use this to find class Q&A, project discussions, lecture questions, or study groups."
    )
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        cid = params.get("course_id")
        return f"Listing Canvas discussions for course {cid}" if cid else "Listing Canvas discussions"
    
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
                        "description": "Optional keyword to search discussion topic titles and messages."
                    },
                    "order_by": {
                        "type": "string",
                        "enum": ["position", "recent_activity", "title"],
                        "default": "recent_activity",
                        "description": "Order of discussions. Defaults to 'recent_activity'."
                    }
                },
                "required": ["course_id"]
            }
        }
        
    async def execute(self, course_id: int, search_term: Optional[str] = None, order_by: str = "recent_activity", **kwargs) -> Dict[str, Any]:
        try:
            params = {
                "per_page": 30,
                "order_by": order_by
            }
            if search_term:
                params["search_term"] = search_term
                
            result = self._manager.get(
                f"/courses/{course_id}/discussion_topics",
                params=params,
                paginate=True
            )
            if not result["success"]:
                return {"success": False, "error": result["error"]}
                
            topics = result["data"]
            if not topics:
                return {"success": True, "result": "No discussion topics found.", "discussions": []}
                
            formatted = []
            for t in topics[:25]:
                formatted.append({
                    "id": t.get("id"),
                    "title": t.get("title", "Untitled Topic"),
                    "author": (t.get("author") or {}).get("display_name", "Unknown"),
                    "posted_at": format_canvas_date(t.get("posted_at") or t.get("created_at")),
                    "last_reply_at": format_canvas_date(t.get("last_reply_at")),
                    "unread_count": t.get("unread_count", 0),
                    "subentries_count": t.get("discussion_subentry_count", 0),
                    "locked": t.get("locked", False),
                    "pinned": t.get("pinned", False),
                    "message_preview": strip_html(t.get("message", ""))[:250]
                })
                
            return {
                "success": True,
                "result": f"Found {len(topics)} discussion topics. Showing top {len(formatted)}.",
                "discussions": formatted
            }
        except Exception as e:
            logger.error(f"Failed to get course discussions: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class ReadDiscussionTopicTool(BaseTool):
    """Tool to read the full content and threaded replies of a Canvas discussion topic."""
    
    name = "read_discussion_topic"
    description = (
        "Read the full prompt and student/instructor replies for a specific Canvas discussion topic."
    )
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        tid = params.get("topic_id")
        cid = params.get("course_id")
        return f"Reading Canvas discussion topic {tid} in course {cid}" if tid and cid else "Reading Canvas discussion topic"
    
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
                    "topic_id": {
                        "type": "integer",
                        "description": "Discussion topic ID from get_course_discussions."
                    },
                    "max_replies": {
                        "type": "integer",
                        "default": 20,
                        "description": "Maximum number of replies to fetch (default: 20)."
                    }
                },
                "required": ["course_id", "topic_id"]
            }
        }
        
    async def execute(self, course_id: int, topic_id: int, max_replies: int = 20, **kwargs) -> Dict[str, Any]:
        try:
            course_id = int(course_id)
            topic_id = int(topic_id)
            max_replies = int(max_replies) if max_replies is not None else 20
            
            # 1. Fetch topic details
            topic_res = self._manager.get(f"/courses/{course_id}/discussion_topics/{topic_id}")
            if not topic_res["success"]:
                return {"success": False, "error": topic_res["error"]}
                
            topic = topic_res["data"]
            
            # 2. Fetch discussion entries/replies
            entries_res = self._manager.get(
                f"/courses/{course_id}/discussion_topics/{topic_id}/entries",
                params={"per_page": min(max_replies, 50)}
            )
            entries = entries_res.get("data", []) if entries_res.get("success") else []
            
            # 3. Format entries
            formatted_replies = []
            for entry in entries[:max_replies]:
                user_name = (entry.get("user") or {}).get("display_name", "Student/Instructor")
                formatted_replies.append({
                    "id": entry.get("id"),
                    "author": user_name,
                    "created_at": format_canvas_date(entry.get("created_at")),
                    "message": strip_html(entry.get("message", ""))
                })
                
            return {
                "success": True,
                "result": f"Read topic '{topic.get('title')}' with {len(formatted_replies)} replies.",
                "topic": {
                    "id": topic.get("id"),
                    "title": topic.get("title"),
                    "author": (topic.get("author") or {}).get("display_name", "Unknown"),
                    "posted_at": format_canvas_date(topic.get("posted_at") or topic.get("created_at")),
                    "prompt": strip_html(topic.get("message", "")),
                    "replies_count": len(formatted_replies),
                    "replies": formatted_replies
                }
            }
        except Exception as e:
            logger.error(f"Failed to read discussion topic: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
