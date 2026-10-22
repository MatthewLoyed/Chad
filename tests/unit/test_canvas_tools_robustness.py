"""Unit tests verifying Canvas tools handle NoneType and string inputs gracefully."""

import unittest
from unittest.mock import MagicMock, patch
import asyncio

from src.tools.canvas.content import ReadCourseFileTool, GetCourseFilesTool
from src.tools.canvas.discussions import ReadDiscussionTopicTool
from src.tools.canvas.assignments import GetRecentGradesTool


class TestCanvasToolsRobustness(unittest.TestCase):
    @patch("requests.get")
    def test_read_course_file_handles_none_max_chars(self, mock_requests_get):
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = {
            "success": True,
            "data": {
                "url": "https://canvas.test/files/123/download",
                "filename": "syllabus.txt",
                "content-type": "text/plain",
            },
        }

        mock_resp = MagicMock()
        mock_resp.content = b"Course Syllabus Details"
        mock_resp.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_resp

        tool = ReadCourseFileTool()
        tool._manager = mock_mgr
        result = asyncio.run(tool.execute(file_id=123, max_chars=None))

        self.assertTrue(result["success"])
        self.assertIn("Course Syllabus Details", result["content"])

    def test_read_discussion_topic_handles_none_max_replies(self):
        mock_mgr = MagicMock()
        mock_mgr.get.side_effect = [
            {"success": True, "data": {"id": 10, "title": "Welcome"}},
            {"success": True, "data": [{"id": 1, "user": {"display_name": "Alice"}, "message": "Hi"}]},
        ]

        tool = ReadDiscussionTopicTool()
        tool._manager = mock_mgr
        result = asyncio.run(tool.execute(course_id=101, topic_id=10, max_replies=None))

        self.assertTrue(result["success"])
        self.assertEqual(len(result["topic"]["replies"]), 1)

    def test_get_recent_grades_handles_none_limit(self):
        mock_mgr = MagicMock()
        mock_mgr.resolve_course_id.return_value = (101, "Data Science", None)
        mock_mgr.get.return_value = {
            "success": True,
            "data": [
                {
                    "assignment_id": 99,
                    "assignment": {"name": "Lab 1", "points_possible": 100},
                    "score": 95,
                    "grade": "A",
                    "graded_at": "2026-10-10T12:00:00Z",
                }
            ],
        }

        tool = GetRecentGradesTool()
        tool._manager = mock_mgr
        result = asyncio.run(tool.execute(course="Data Science", limit=None))

        self.assertTrue(result["success"])
        self.assertEqual(len(result["grades"]), 1)

    def test_get_course_files_403_returns_locked_files_guidance(self):
        mock_mgr = MagicMock()
        mock_mgr.resolve_course_id.return_value = (186512, "Wireless Networks", None)
        mock_mgr.get.return_value = {
            "success": False,
            "error": "HTTP 403 Client Error: Forbidden for url: https://canvas.instructure.com/api/v1/courses/186512/files",
        }

        tool = GetCourseFilesTool()
        tool._manager = mock_mgr
        result = asyncio.run(tool.execute(course="Wireless Networks"))

        self.assertFalse(result["success"])
        self.assertEqual(result.get("error_code"), "CANVAS_LOCKED_FILES")
        self.assertIn("search_module_items", result["error"])
        self.assertIn("search_for_tool", result["error"])


if __name__ == "__main__":
    unittest.main()
