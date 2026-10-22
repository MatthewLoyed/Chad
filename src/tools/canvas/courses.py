"""
Canvas Course & Module Tools
Tools for listing courses and navigating course modules.
"""

from typing import Dict, Any, Optional, Union
from src.tools.base_tool import BaseTool
from src.tools.canvas.manager import CanvasManager, logger


class GetCanvasCoursesTool(BaseTool):
    """Tool to fetch or search active Canvas courses."""
    
    name = "get_canvas_courses"
    description = (
        "List all your current active Canvas courses or search for a course by name/code (e.g. search_term='software'). "
        "Returns course names, codes, and IDs."
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
                    "search_term": {
                        "type": "string",
                        "description": "Optional keyword or title to filter courses by name or code (e.g. 'Software', 'Data Science')."
                    }
                }
            }
        }
    
    async def execute(self, search_term: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            result = self._manager.get(
                "/courses",
                params={
                    "enrollment_state": "active",
                    "include[]": ["term"],
                    "per_page": 50
                },
                paginate=True
            )
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            courses = result["data"]
            if not courses:
                return {
                    "success": True,
                    "result": "No active courses found on Canvas.",
                    "courses": []
                }
            
            formatted_courses = []
            search_lower = (search_term or "").strip().lower()
            
            for course in courses:
                cname = course.get("name", "Untitled Course")
                ccode = course.get("course_code", "")
                
                if search_lower:
                    if search_lower not in cname.lower() and search_lower not in ccode.lower():
                        continue
                        
                formatted_courses.append({
                    "id": course.get("id"),
                    "name": cname,
                    "course_code": ccode,
                    "term": course.get("term", {}).get("name", "Unknown Term") if course.get("term") else "Unknown Term"
                })
            
            if not formatted_courses and search_term:
                return {
                    "success": True,
                    "result": f"No active courses matched '{search_term}'.",
                    "courses": []
                }
                
            return {
                "success": True,
                "result": f"Found {len(formatted_courses)} active courses.",
                "courses": formatted_courses
            }
            
        except Exception as e:
            logger.error(f"Failed to get Canvas courses: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetCourseModulesTool(BaseTool):
    """Tool to list all modules in a Canvas course with their items."""
    
    name = "get_course_modules"
    description = (
        "List all modules in a Canvas course. You can provide either the course ID (e.g. 180189) "
        "or course name (e.g. 'Manage Software Project'). Modules organize coursework into units/weeks."
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
                        "description": "Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project')."
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
            cid, _, err = self._manager.resolve_course_id(course_id or course or kwargs.get("course_name"))
            if err:
                return {"success": False, "error": err}
                
            result = self._manager.get(
                f"/courses/{cid}/modules",
                params={"per_page": 50},
                paginate=True
            )
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            modules = result["data"]
            if not modules:
                return {
                    "success": True,
                    "result": f"No modules found for course {cid}.",
                    "modules": []
                }
            
            formatted_modules = []
            for module in modules:
                formatted_modules.append({
                    "id": module.get("id"),
                    "name": module.get("name", "Untitled Module"),
                    "item_count": module.get("items_count", 0),
                    "state": module.get("state", "unknown")
                })
            
            return {
                "success": True,
                "result": f"Found {len(formatted_modules)} modules for course {cid}.",
                "modules": formatted_modules
            }
            
        except Exception as e:
            logger.error(f"Failed to get course modules: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetModuleItemsTool(BaseTool):
    """Tool to get all items within a specific module."""
    
    name = "get_module_items"
    description = "List items within a specific Canvas module (assignments, files, pages, sub-headers)."
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
                        "description": "Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project')."
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Optional numeric course ID."
                    },
                    "module_id": {
                        "type": "integer",
                        "description": "Module ID from get_course_modules."
                    }
                },
                "required": ["module_id"]
            }
        }
    
    async def execute(self, module_id: int, course: Optional[Union[str, int]] = None, course_id: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        try:
            cid, _, err = self._manager.resolve_course_id(course_id or course or kwargs.get("course_name"))
            if err:
                return {"success": False, "error": err}
                
            result = self._manager.get(
                f"/courses/{cid}/modules/{module_id}/items",
                params={"per_page": 50},
                paginate=True
            )
            
            if not result["success"]:
                return {"success": False, "error": result["error"]}
            
            items = result["data"]
            if not items:
                return {
                    "success": True,
                    "result": "No items found in this module.",
                    "items": []
                }
            
            formatted_items = []
            for item in items:
                formatted_items.append({
                    "id": item.get("id"),
                    "title": item.get("title", "Untitled Item"),
                    "type": item.get("type"),
                    "content_id": item.get("content_id"),
                    "html_url": item.get("html_url", "")
                })
            
            return {
                "success": True,
                "result": f"Found {len(formatted_items)} items in module.",
                "items": formatted_items
            }
            
        except Exception as e:
            logger.error(f"Failed to get module items: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class SearchModuleItemsTool(BaseTool):
    """Tool to search for items across all modules in a course."""
    
    name = "search_module_items"
    description = (
        "Search for specific items (pages, files, assignments) across ALL modules in a course. "
        "Accepts course ID or course name (e.g. course='Manage Software Project', search_term='Assignment')."
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
                        "description": "Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project')."
                    },
                    "course_id": {
                        "type": "integer",
                        "description": "Optional numeric course ID."
                    },
                    "search_term": {
                        "type": "string",
                        "description": "Term to search for (e.g., 'syllabus', 'assignment', 'quiz')."
                    }
                },
                "required": ["search_term"]
            }
        }
    
    async def execute(self, search_term: str, course: Optional[Union[str, int]] = None, course_id: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        try:
            cid, _, err = self._manager.resolve_course_id(course_id or course or kwargs.get("course_name"))
            if err:
                return {"success": False, "error": err}
                
            modules_result = self._manager.get(
                f"/courses/{cid}/modules",
                params={"per_page": 50, "include[]": "items"},
                paginate=True
            )
            
            if not modules_result["success"]:
                return {"success": False, "error": modules_result["error"]}
            
            modules = modules_result["data"]
            matches = []
            search_lower = search_term.lower()
            
            for module in modules:
                items = module.get("items", [])
                for item in items:
                    title = item.get("title", "").lower()
                    if search_lower in title:
                        matches.append({
                            "module_name": module.get("name"),
                            "item_id": item.get("id"),
                            "title": item.get("title"),
                            "type": item.get("type"),
                            "content_id": item.get("content_id"),
                            "html_url": item.get("html_url", "")
                        })
            
            if not matches:
                return {
                    "success": True,
                    "result": f"No module items found matching '{search_term}'.",
                    "matches": []
                }
            
            return {
                "success": True,
                "result": f"Found {len(matches)} matches for '{search_term}'.",
                "matches": matches[:20]
            }
            
        except Exception as e:
            logger.error(f"Failed to search module items: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
