"""
Canvas System Tools Package
Exposes all Canvas tools for easy import and auto-discovery.
"""

# Re-export tools from modules for backward compatibility and registry discovery
from src.tools.canvas.manager import CanvasManager
from src.tools.canvas.courses import (
    GetCanvasCoursesTool,
    GetCourseModulesTool,
    GetModuleItemsTool,
    SearchModuleItemsTool,
)
from src.tools.canvas.assignments import (
    GetCanvasAssignmentsTool,
    GetUpcomingAssignmentsTool,
    GetRecentGradesTool,
    GetAssignmentDetailsTool,
    GetCanvasDashboardTodosTool,
    GetAssignmentSubmissionTool,
)
from src.tools.canvas.content import (
    GetCourseAnnouncementsTool,
    GetCourseSyllabusTool,
    GetCourseFilesTool,
    ReadCourseFileTool,
    GetCoursePagesTool,
    ReadCoursePageTool,
    DownloadCourseFileTool,
)
from src.tools.canvas.people import GetCoursePeopleTool
from src.tools.canvas.grades import GetCourseGradesTool
from src.tools.canvas.discussions import (
    GetCourseDiscussionsTool,
    ReadDiscussionTopicTool,
)
from src.tools.canvas.calendar import GetCanvasCalendarEventsTool
from src.tools.canvas.quizzes import GetCourseQuizzesTool
from src.tools.canvas.activity import GetCanvasActivityStreamTool

# Expose all tools in __init__ for wildcard imports
__all__ = [
    'CanvasManager',
    'GetCanvasCoursesTool',
    'GetCourseModulesTool',
    'GetModuleItemsTool',
    'SearchModuleItemsTool',
    'GetCanvasAssignmentsTool',
    'GetUpcomingAssignmentsTool',
    'GetRecentGradesTool',
    'GetAssignmentDetailsTool',
    'GetCanvasDashboardTodosTool',
    'GetAssignmentSubmissionTool',
    'GetCourseAnnouncementsTool',
    'GetCourseSyllabusTool',
    'GetCourseFilesTool',
    'ReadCourseFileTool',
    'GetCoursePagesTool',
    'ReadCoursePageTool',
    'DownloadCourseFileTool',
    'GetCoursePeopleTool',
    'GetCourseGradesTool',
    'GetCourseDiscussionsTool',
    'ReadDiscussionTopicTool',
    'GetCanvasCalendarEventsTool',
    'GetCourseQuizzesTool',
    'GetCanvasActivityStreamTool',
]
