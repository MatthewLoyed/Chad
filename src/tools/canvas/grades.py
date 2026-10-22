"""
Canvas Grades & Score Tools
Tools for retrieving current course grades, standing, and grading breakdowns.
"""

from typing import Dict, Any, Optional, List, Union
from src.tools.base_tool import BaseTool
from src.tools.canvas.manager import CanvasManager, logger


class GetCourseGradesTool(BaseTool):
    """Tool to fetch current overall course grades and standings across active courses."""
    
    name = "get_course_grades"
    description = (
        "Get overall grades, percentage scores, and current academic standing for enrolled Canvas courses. "
        "Can fetch all courses or a specific course (by name or ID, e.g. course='Manage Software Project')."
    )
    category = "canvas"
    requirements = ["CANVAS_TOKEN", "CANVAS_BASE_URL"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        course = params.get("course")
        return f"Fetching Canvas grades for course '{course}'" if course else "Fetching Canvas grades"
    
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
                        "description": "Optional: Canvas course ID (e.g. 180189) OR course name (e.g. 'Manage Software Project'). Omit for all active courses."
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
            if target_course:
                cid, cname, err = self._manager.resolve_course_id(target_course)
                if err:
                    return {"success": False, "error": err}
                # Get enrollment for specific course
                result = self._manager.get(
                    f"/courses/{cid}/enrollments",
                    params={"user_id": "self", "include[]": ["grades", "current_points"]}
                )
                if not result["success"]:
                    return {"success": False, "error": result["error"]}
                
                enrollments = result["data"]
                course_name = cname or f"Course {cid}"
                
                grade_records = []
                for enr in enrollments:
                    grades = enr.get("grades", {})
                    grade_records.append({
                        "course_id": cid,
                        "course_name": course_name,
                        "role": enr.get("type", "StudentEnrollment"),
                        "current_score": grades.get("current_score"),
                        "current_grade": grades.get("current_grade"),
                        "final_score": grades.get("final_score"),
                        "final_grade": grades.get("final_grade"),
                        "unposted_current_score": grades.get("unposted_current_score"),
                        "unposted_current_grade": grades.get("unposted_current_grade")
                    })
                
                return {
                    "success": True,
                    "result": f"Found grade info for {course_name}",
                    "grades": grade_records
                }
            else:
                # Get grades across all active courses
                result = self._manager.get(
                    "/courses",
                    params={
                        "enrollment_state": "active",
                        "include[]": ["total_scores", "current_grading_period_scores", "term"]
                    },
                    paginate=True
                )
                if not result["success"]:
                    return {"success": False, "error": result["error"]}
                
                courses = result["data"]
                grade_records = []
                for c in courses:
                    enrollments = c.get("enrollments") or []
                    for enr in enrollments:
                        # Only student-type grades
                        current_score = enr.get("computed_current_score")
                        current_grade = enr.get("computed_current_grade")
                        final_score = enr.get("computed_final_score")
                        final_grade = enr.get("computed_final_grade")
                        
                        grade_records.append({
                            "course_id": c.get("id"),
                            "course_name": c.get("name"),
                            "course_code": c.get("course_code"),
                            "current_score": current_score,
                            "current_grade": current_grade,
                            "final_score": final_score,
                            "final_grade": final_grade
                        })
                
                return {
                    "success": True,
                    "result": f"Retrieved grades for {len(grade_records)} courses.",
                    "grades": grade_records
                }
        except Exception as e:
            logger.error(f"Failed to get course grades: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
