"""
Canvas Assignments & To-Do Tools
Tools for retrieving assignments, grades, and global dashboard items.
"""

import re
from datetime import datetime
from typing import Dict, Any, Optional, List, Union
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


class GetCanvasAssignmentsTool(BaseTool):
    """Tool to fetch all or filtered assignments from a Canvas course."""
    
    name = "get_canvas_assignments"
    description = (
        "Get coursework and assignments from Canvas. Supports passing either course ID (e.g. 180189) "
        "OR course name (e.g. 'Manage Software Project', 'Data Science'). "
        "If course is omitted and search_term is provided, searches across all active courses. "
        "Supports filtering by bucket ('all', 'upcoming', 'past', 'overdue') and keyword search."
    )
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        course = params.get("course")
        term = params.get("search_term")
        if course and term:
            return f"Searching Canvas assignments in '{course}' for '{term}'"
        elif course:
            return f"Listing Canvas assignments for '{course}'"
        elif term:
            return f"Searching Canvas assignments across courses for '{term}'"
        return "Listing Canvas assignments"
    
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
                    "course": {
                        "type": "string",
                        "description": "Optional: Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project'). If omitted with search_term, searches across all active courses."
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Optional numeric course ID."
                    },
                    "bucket": {
                        "type": "string",
                        "enum": ["all", "upcoming", "past", "overdue", "undated", "ungraded"],
                        "description": "Assignment category filter. Defaults to 'all'. Use 'past' for past homework, 'overdue' for missed work, 'upcoming' for future tasks.",
                        "default": "all"
                    },
                    "search_term": {
                        "type": "string",
                        "description": "Optional keyword or title to search for specific assignments (e.g. 'Assignment 4', 'Quiz')."
                    },
                    "order_by": {
                        "type": "string",
                        "enum": ["due_at", "name", "position"],
                        "description": "Order of returned assignments. Defaults to 'due_at'.",
                        "default": "due_at"
                    }
                }
            }
        }
    
    async def execute(
        self,
        course: Optional[Union[str, int]] = None,
        course_id: Optional[int] = None,
        bucket: str = "all",
        search_term: Optional[str] = None,
        order_by: str = "due_at",
        **kwargs
    ) -> Dict[str, Any]:
        try:
            target_course = course_id or course or kwargs.get("course_name")
            if not target_course:
                if search_term and str(search_term).strip():
                    matches, err = self._manager.search_assignments_globally(str(search_term).strip())
                    if err:
                        return {"success": False, "error": err}
                    if not matches:
                        return {
                            "success": True,
                            "result": f"No assignments found matching '{search_term}' across active courses.",
                            "assignments": []
                        }
                    formatted = []
                    for a in matches:
                        formatted.append({
                            "id": a.get("id"),
                            "name": a.get("name", "Untitled Assignment"),
                            "course_id": a.get("course_id"),
                            "course_name": a.get("course_name"),
                            "due_at": format_canvas_date(a.get("due_at")),
                            "points_possible": a.get("points_possible", 0),
                            "submission_types": a.get("submission_types", []),
                            "has_submitted_submissions": a.get("has_submitted_submissions", False),
                            "locked_for_user": a.get("locked_for_user", False),
                            "html_url": a.get("html_url", ""),
                        })
                    return {
                        "success": True,
                        "result": f"Found {len(formatted)} assignment(s) across courses matching '{search_term}'.",
                        "assignments": formatted
                    }
                else:
                    # If no course and no search_term, list upcoming across all active courses
                    up_tool = GetUpcomingAssignmentsTool()
                    return await up_tool.execute()

            cid, cname, err = self._manager.resolve_course_id(target_course)
            if err:
                return {"success": False, "error": err}
                
            params: Dict[str, Any] = {
                "order_by": order_by or "due_at",
                "per_page": 50,
            }
            bucket_val = (bucket or "all").strip().lower()
            if bucket_val != "all":
                params["bucket"] = bucket_val
            
            if search_term and str(search_term).strip():
                params["search_term"] = str(search_term).strip()

            result = self._manager.get(
                f"/courses/{cid}/assignments",
                params=params,
                paginate=True
            )
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            assignments = result["data"]
            if not assignments:
                course_label = f"course '{cname}' ({cid})" if cname else f"course {cid}"
                match_note = f" matching '{search_term}'" if search_term else ""
                return {
                    "success": True,
                    "result": f"No assignments found for {course_label} with filter bucket='{bucket_val}'{match_note}.",
                    "assignments": []
                }
            
            formatted = []
            for a in assignments:
                due_str = format_canvas_date(a.get("due_at"))
                lock_at_str = format_canvas_date(a.get("lock_at")) if a.get("lock_at") else None
                formatted.append({
                    "id": a.get("id"),
                    "name": a.get("name", "Untitled Assignment"),
                    "due_at": due_str,
                    "lock_at": lock_at_str,
                    "points_possible": a.get("points_possible", 0),
                    "submission_types": a.get("submission_types", []),
                    "has_submitted_submissions": a.get("has_submitted_submissions", False),
                    "locked_for_user": a.get("locked_for_user", False),
                    "html_url": a.get("html_url", ""),
                })
            
            course_label = f"course '{cname}' ({cid})" if cname else f"course {cid}"
            return {
                "success": True,
                "result": f"Found {len(formatted)} assignments for {course_label}.",
                "course_id": cid,
                "bucket": bucket_val,
                "assignments": formatted
            }
        except Exception as e:
            logger.error(f"Failed to get canvas assignments: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetUpcomingAssignmentsTool(BaseTool):
    """Tool to fetch upcoming assignments from Canvas courses."""
    
    name = "get_upcoming_assignments"
    description = (
        "Get upcoming assignments across all active courses or for a specific course (by name or ID). "
        "Returns upcoming due dates, assignment titles, points, and submission types."
    )
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
                    "course": {
                        "type": "string",
                        "description": "Optional: Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project'). Omit for all courses."
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Optional numeric course ID."
                    }
                }
            }
        }
    
    async def execute(self, course: Optional[Union[str, int]] = None, course_id: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        try:
            target_course = course_id or course or kwargs.get("course_name")
            all_formatted = []
            
            if target_course:
                cid, cname, err = self._manager.resolve_course_id(target_course)
                if err:
                    return {"success": False, "error": err}
                courses_to_query = [{"id": cid, "name": cname or f"Course {cid}"}]
            else:
                courses_res = self._manager.get("/courses", params={"enrollment_state": "active", "per_page": 30}, paginate=True)
                courses_to_query = courses_res.get("data", []) if courses_res.get("success") else []
                
            for c in courses_to_query:
                cid = c.get("id")
                cname = c.get("name", f"Course {cid}")
                result = self._manager.get(
                    f"/courses/{cid}/assignments",
                    params={
                        "bucket": "upcoming",
                        "order_by": "due_at",
                        "per_page": 20
                    }
                )
                if not result.get("success"):
                    continue
                    
                assignments = result.get("data") or []
                for assignment in assignments:
                    due_at = assignment.get("due_at")
                    all_formatted.append({
                        "id": assignment.get("id"),
                        "course_id": cid,
                        "course_name": cname,
                        "name": assignment.get("name", "Untitled Assignment"),
                        "due_at": format_canvas_date(due_at),
                        "points_possible": assignment.get("points_possible", 0),
                        "submission_types": assignment.get("submission_types", []),
                        "html_url": assignment.get("html_url", "")
                    })
            
            if not all_formatted:
                return {
                    "success": True,
                    "result": "No upcoming assignments found.",
                    "assignments": []
                }
            
            return {
                "success": True,
                "result": f"Found {len(all_formatted)} upcoming assignments.",
                "assignments": all_formatted
            }
        except Exception as e:
            logger.error(f"Failed to get upcoming assignments: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetRecentGradesTool(BaseTool):
    """Tool to fetch recent graded submissions from Canvas courses."""
    
    name = "get_recent_grades"
    description = "Retrieve the most recent graded submissions and scores across active courses or for a specific course."
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
                    "course": {
                        "type": "string",
                        "description": "Optional: Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project')."
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Optional numeric course ID."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of graded submissions to return (default: 10)",
                        "default": 10
                    }
                }
            }
        }
    
    async def execute(self, course: Optional[Union[str, int]] = None, course_id: Optional[int] = None, limit: int = 10, **kwargs) -> Dict[str, Any]:
        try:
            limit = int(limit) if limit is not None else 10
            target_course = course_id or course or kwargs.get("course_name")
            courses_to_query = []
            
            if target_course:
                cid, cname, err = self._manager.resolve_course_id(target_course)
                if err:
                    return {"success": False, "error": err}
                courses_to_query = [{"id": cid, "name": cname or f"Course {cid}"}]
            else:
                courses_res = self._manager.get("/courses", params={"enrollment_state": "active", "per_page": 20}, paginate=True)
                courses_to_query = courses_res.get("data", []) if courses_res.get("success") else []
                
            formatted_grades = []
            for c in courses_to_query:
                cid = c.get("id")
                cname = c.get("name", f"Course {cid}")
                result = self._manager.get(
                    f"/courses/{cid}/students/submissions",
                    params={
                        "student_ids[]": "self",
                        "include[]": ["assignment"],
                        "workflow_state": "graded",
                        "order": "graded_at",
                        "order_direction": "descending",
                        "per_page": limit
                    }
                )
                if not result.get("success"):
                    continue
                    
                submissions = result.get("data") or []
                for submission in submissions:
                    graded_at = submission.get("graded_at")
                    assignment = submission.get("assignment", {})
                    points_possible = assignment.get("points_possible") or 0
                    score = submission.get("score")
                    grade = submission.get("grade", "")
                    
                    percentage = ""
                    if score is not None and points_possible and points_possible > 0:
                        pct_val = (score / points_possible) * 100
                        percentage = f"{pct_val:.1f}%"
                    
                    formatted_grades.append({
                        "course_id": cid,
                        "course_name": cname,
                        "assignment_id": submission.get("assignment_id"),
                        "assignment_name": assignment.get("name", "Unknown Assignment"),
                        "score": score,
                        "points_possible": points_possible,
                        "grade": grade,
                        "percentage": percentage,
                        "graded_at": format_canvas_date(graded_at),
                        "late": submission.get("late", False),
                        "missing": submission.get("missing", False)
                    })
                    
            if not formatted_grades:
                return {
                    "success": True,
                    "result": "No recently graded submissions found.",
                    "grades": []
                }
                
            return {
                "success": True,
                "result": f"Found {len(formatted_grades)} recent graded submissions.",
                "grades": formatted_grades[:limit]
            }
        except Exception as e:
            logger.error(f"Failed to get recent grades: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetAssignmentDetailsTool(BaseTool):
    """Tool to fetch detailed instructions/content for a specific assignment."""
    
    name = "get_assignment_details"
    description = (
        "Get full instructions, prompt, point breakdown, and attached file links for a specific Canvas assignment. "
        "Accepts assignment title/ID and optional course name/ID (e.g. assignment='Assignment 4', course='Manage Software Project'). "
        "If course is omitted, searches across all active courses automatically. "
        "If the prompt references an attached PDF, use read_course_file with the returned file_id."
    )
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
                    "assignment": {
                        "type": "string",
                        "description": "Canvas assignment title (e.g. 'Assignment 4', 'Microsoft Project Assignment 4') OR assignment ID (e.g. 1839938)."
                    },
                    "course": {
                        "type": "string",
                        "description": "Optional: Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project'). If omitted, searches across all active courses automatically."
                    },
                    "assignment_id": {
                        "type": "integer",
                        "description": "Optional numeric assignment ID."
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Optional numeric course ID."
                    }
                },
                "required": ["assignment"]
            }
        }
    
    def _strip_html(self, html_content: str) -> str:
        """Strip HTML tags from content and clean up whitespace."""
        if not html_content:
            return ""
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?(p|div|br|li|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        return text.strip()

    def _extract_file_links(self, html_content: str) -> List[Dict[str, Any]]:
        """Extract Canvas file IDs and names from description HTML."""
        if not html_content:
            return []
            
        file_links = []
        pattern = r'/files/(\d+).*?title="([^"]*)"'
        matches = re.finditer(pattern, html_content)
        for match in matches:
            file_id = match.group(1)
            file_name = match.group(2)
            file_links.append({
                "file_id": int(file_id),
                "display_name": file_name
            })
            
        pattern_api = r'data-api-endpoint="[^"]*/files/(\d+)".*?>([^<]*)</a>'
        matches_api = re.finditer(pattern_api, html_content)
        for match in matches_api:
            file_id = match.group(1)
            file_name_raw = match.group(2).strip()
            file_name = re.sub(r'<[^>]+>', '', file_name_raw)
            if not any(f["file_id"] == int(file_id) for f in file_links):
                file_links.append({
                    "file_id": int(file_id),
                    "display_name": file_name
                })
                
        return file_links
    
    async def execute(
        self,
        course: Optional[Union[str, int]] = None,
        assignment: Optional[Union[str, int]] = None,
        course_id: Optional[int] = None,
        assignment_id: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        try:
            target_course = course_id or course or kwargs.get("course_name")
            target_assignment = assignment_id or assignment or kwargs.get("assignment_name")
            
            if not target_assignment:
                return {"success": False, "error": "assignment (name or ID) is required."}

            if not target_course:
                # Cross-course search across all active courses when course is omitted
                matches, err = self._manager.search_assignments_globally(target_assignment)
                if err:
                    return {"success": False, "error": err}
                if not matches:
                    return {
                        "success": False,
                        "error": f"No assignment matching '{target_assignment}' was found across your active courses."
                    }
                
                # Single match or clear top match
                if len(matches) == 1:
                    cid = matches[0]["course_id"]
                    cname = matches[0]["course_name"]
                    aid = matches[0]["id"]
                else:
                    top_score = matches[0].get("_match_score", 0)
                    second_score = matches[1].get("_match_score", 0)
                    unique_courses = set(m.get("course_id") for m in matches)
                    if len(unique_courses) == 1 or (top_score >= 80 and top_score - second_score >= 25):
                        cid = matches[0]["course_id"]
                        cname = matches[0]["course_name"]
                        aid = matches[0]["id"]
                    else:
                        options = [
                            f"'{m.get('name')}' in {m.get('course_name')} (course_id: {m.get('course_id')}, assignment_id: {m.get('id')})"
                            for m in matches[:5]
                        ]
                        return {
                            "success": False,
                            "error": (
                                f"Multiple matching assignments found for '{target_assignment}'. "
                                f"Please specify the course or assignment_id:\n- " + "\n- ".join(options)
                            ),
                            "matches": matches[:5]
                        }
            else:
                cid, cname, cerr = self._manager.resolve_course_id(target_course)
                if cerr:
                    return {"success": False, "error": cerr}
                    
                aid, aname, aerr = self._manager.resolve_assignment_id(cid, target_assignment)
                if aerr:
                    return {"success": False, "error": aerr}

            result = self._manager.get(f"/courses/{cid}/assignments/{aid}")
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            assignment_data = result["data"]
            raw_description = assignment_data.get("description", "")
            instructions = self._strip_html(raw_description)
            
            discussion_content = ""
            if assignment_data.get("discussion_topic"):
                discussion_topic = assignment_data["discussion_topic"]
                raw_message = discussion_topic.get("message", "")
                discussion_content = self._strip_html(raw_message)
            
            due_at = assignment_data.get("due_at")
            due_date_str = ""
            if due_at:
                try:
                    due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                    due_date_str = due_dt.strftime("%B %d, %Y at %I:%M %p")
                except ValueError:
                    due_date_str = due_at
            
            attachments = self._extract_file_links(raw_description)
            
            response = {
                "success": True,
                "result": f"Retrieved assignment details for '{assignment_data.get('name', 'Unknown')}'",
                "assignment": {
                    "id": assignment_data.get("id"),
                    "course_id": cid,
                    "course_name": cname,
                    "name": assignment_data.get("name", "Untitled Assignment"),
                    "due_at": due_date_str or "No due date",
                    "points_possible": assignment_data.get("points_possible", 0),
                    "submission_types": assignment_data.get("submission_types", []),
                    "instructions": instructions[:4000] if instructions else "No written instructions provided.",
                    "attachments": attachments
                }
            }
            
            if attachments:
                response["note"] = "Instructions may refer to attached files. Use read_course_file with the provided file_id to read PDF/Doc contents."
            
            if discussion_content and discussion_content != instructions:
                response["assignment"]["discussion_prompt"] = discussion_content[:1500]
            
            return response
            
        except Exception as e:
            logger.error(f"Failed to get assignment details: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetCanvasDashboardTodosTool(BaseTool):
    """Tool to fetch the current user's global To-Do list from Canvas dashboard."""
    
    name = "get_canvas_dashboard_todos"
    description = "Get your global To-Do list (all upcoming assignments, quizzes, etc.) from the Canvas dashboard. Use this for general queries like 'what is due', 'what homework do I have', or 'check my dashboard'."
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
                "properties": {},
                "required": []
            }
        }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            result = self._manager.get("/users/self/todo")
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            todo_items = result["data"]
            if not todo_items:
                return {
                    "success": True,
                    "result": "Your Canvas To-Do list is empty.",
                    "todos": []
                }
            
            formatted_todos = []
            for item in todo_items:
                item_type = item.get("type", "unknown")
                assignment = item.get("assignment", {}) if item_type == "assignment" else {}
                
                due_at = item.get("due_at") or assignment.get("due_at")
                due_date_str = ""
                if due_at:
                    try:
                        due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                        due_date_str = due_dt.strftime("%B %d, %Y at %I:%M %p")
                    except ValueError:
                        due_date_str = due_at
                
                formatted_todos.append({
                    "id": item.get("id"),
                    "type": item_type,
                    "title": item.get("assignment", {}).get("name") or item.get("quiz", {}).get("title") or "Untitled",
                    "course_id": item.get("course_id"),
                    "due_at": due_date_str or "No due date",
                    "html_url": item.get("html_url", ""),
                    "points_possible": assignment.get("points_possible") if item_type == "assignment" else None
                })
            
            return {
                "success": True,
                "result": f"Found {len(formatted_todos)} items in your global To-Do list.",
                "todos": formatted_todos
            }
            
        except Exception as e:
            logger.error(f"Failed to get dashboard todos: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetAssignmentSubmissionTool(BaseTool):
    """Tool to fetch a student's submission, score, rubric assessment, and instructor feedback comments."""
    
    name = "get_assignment_submission"
    description = (
        "Get submission details, grades, scores, teacher feedback comments, and rubric grading breakdown "
        "for a specific Canvas assignment. Accepts either IDs or names (e.g. course='Manage Software Project', assignment='Assignment 4')."
    )
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        asg = params.get("assignment", "")
        course = params.get("course", "")
        return f"Fetching Canvas submission for '{asg}' ({course})" if asg else "Fetching Canvas assignment submission"
    
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
                    "course": {
                        "type": "string",
                        "description": "Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project')."
                    },
                    "assignment": {
                        "type": "string",
                        "description": "Canvas assignment ID (e.g. 1839938) OR assignment title (e.g. 'Assignment 4', 'Microsoft Project Assignment 4')."
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Optional numeric course ID."
                    },
                    "assignment_id": {
                        "type": "integer",
                        "description": "Optional numeric assignment ID."
                    }
                }
            }
        }
    
    async def execute(
        self,
        course: Optional[Union[str, int]] = None,
        assignment: Optional[Union[str, int]] = None,
        course_id: Optional[int] = None,
        assignment_id: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        try:
            target_course = course_id or course or kwargs.get("course_name")
            target_assignment = assignment_id or assignment or kwargs.get("assignment_name")
            
            cid, cname, cerr = self._manager.resolve_course_id(target_course)
            if cerr:
                return {"success": False, "error": cerr}
                
            aid, aname, aerr = self._manager.resolve_assignment_id(cid, target_assignment)
            if aerr:
                return {"success": False, "error": aerr}

            result = self._manager.get(
                f"/courses/{cid}/assignments/{aid}/submissions/self",
                params={
                    "include[]": ["submission_comments", "rubric_assessment", "submission_history"]
                }
            )
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            sub = result["data"]
            if not sub:
                return {"success": True, "result": "No submission record found.", "submission": None}
            
            submitted_at = format_canvas_date(sub.get("submitted_at"))
            graded_at = format_canvas_date(sub.get("graded_at")) if sub.get("graded_at") else None
            
            raw_comments = sub.get("submission_comments") or []
            comments = []
            for c in raw_comments:
                comments.append({
                    "author": c.get("author_name") or c.get("author", {}).get("display_name", "Instructor"),
                    "comment": c.get("comment", ""),
                    "created_at": format_canvas_date(c.get("created_at")),
                    "attachments": [att.get("url") for att in (c.get("attachments") or []) if att.get("url")]
                })
            
            rubric_raw = sub.get("rubric_assessment") or {}
            rubric_scores = []
            if isinstance(rubric_raw, dict):
                for crit_id, crit_data in rubric_raw.items():
                    if isinstance(crit_data, dict):
                        rubric_scores.append({
                            "criterion_id": crit_id,
                            "points": crit_data.get("points"),
                            "comments": crit_data.get("comments") or "No comment"
                        })
            
            formatted_sub = {
                "assignment_id": aid,
                "assignment_name": aname,
                "course_id": cid,
                "course_name": cname,
                "workflow_state": sub.get("workflow_state", "unsubmitted"),
                "submitted_at": submitted_at,
                "graded_at": graded_at,
                "score": sub.get("score"),
                "grade": sub.get("grade"),
                "points_deducted": sub.get("points_deducted"),
                "late": sub.get("late", False),
                "missing": sub.get("missing", False),
                "attempt": sub.get("attempt", 1),
                "submission_type": sub.get("submission_type"),
                "submitted_url": sub.get("url"),
                "body_preview": sub.get("body", "")[:300] if sub.get("body") else None,
                "instructor_comments": comments,
                "rubric_assessment": rubric_scores,
            }
            
            state_desc = formatted_sub["workflow_state"].capitalize()
            score_desc = f"Score: {formatted_sub['score']}" if formatted_sub['score'] is not None else "Not graded yet"
            return {
                "success": True,
                "result": f"Submission status: {state_desc}. {score_desc}. Comments: {len(comments)}.",
                "submission": formatted_sub
            }
        except Exception as e:
            logger.error(f"Failed to get assignment submission: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
