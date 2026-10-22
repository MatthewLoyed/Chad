"""Unit tests for scoped tool discovery and tiered bundle exposure."""

import unittest
import asyncio
from unittest.mock import MagicMock

from src.infrastructure.tools.registry import ToolRegistry
from src.tools.system.utilities.tool_search import ToolSearchTool
from src.infrastructure.tools.selection.turn_selector import TurnToolSelector


class TestScopedToolDiscovery(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_all()

    def test_search_tools_with_category_scopes_to_canvas(self):
        # Search specifically for files within category="canvas"
        schemas = self.registry.search_tools("download assignment files", limit=5, category="canvas")
        
        self.assertGreater(len(schemas), 0)
        schema_names = [s["name"] for s in schemas]
        
        # Must only return Canvas tools
        for name in schema_names:
            tool = self.registry.get_tool(name)
            self.assertEqual(tool.category, "canvas")
            
        # Should match file tools
        self.assertTrue(
            "download_course_file" in schema_names or "read_course_file" in schema_names or "get_course_files" in schema_names
        )
        # Must NOT match general system/os tools
        self.assertNotIn("download_file", schema_names)
        self.assertNotIn("read_file", schema_names)

    def test_tool_search_tool_execute_with_category(self):
        tool_search = ToolSearchTool(registry=self.registry)
        
        result = asyncio.run(tool_search.execute(query="syllabus", category="canvas"))
        
        self.assertTrue(result["success"])
        res_dict = result["result"]
        self.assertIn("in category 'canvas'", res_dict["message"])
        
        found_names = [t["name"] for t in res_dict["tools"]]
        self.assertIn("get_course_syllabus", found_names)
        
        # Metadata for orchestrator injection is present
        injected = res_dict.get("_injected_schemas", [])
        self.assertGreater(len(injected), 0)

    def test_canvas_bundle_is_tiered(self):
        selector = TurnToolSelector(self.registry)
        
        # Expanding a core gateway tool only expands core canvas tools (6 tools)
        core_expanded = selector._expand_bundles(["get_assignment_details"])
        self.assertIn("get_canvas_courses", core_expanded)
        self.assertIn("get_canvas_assignments", core_expanded)
        self.assertIn("get_upcoming_assignments", core_expanded)
        self.assertIn("get_assignment_details", core_expanded)
        self.assertIn("read_course_file", core_expanded)
        self.assertIn("get_course_syllabus", core_expanded)
        
        # Must NOT dump deep tools into the core bundle
        self.assertNotIn("get_course_discussions", core_expanded)
        self.assertNotIn("read_discussion_topic", core_expanded)
        self.assertNotIn("get_course_quizzes", core_expanded)
        self.assertNotIn("download_course_file", core_expanded)
        self.assertEqual(len(core_expanded), 6)

    def test_canvas_files_bundle_expands_specifically(self):
        selector = TurnToolSelector(self.registry)
        
        files_expanded = selector._expand_bundles(["download_course_file"])
        self.assertIn("get_course_files", files_expanded)
        self.assertNotIn("get_course_discussions", files_expanded)

    def test_all_canvas_bundles_load_canvas_skill(self):
        selector = TurnToolSelector(self.registry)
        
        self.assertEqual(selector._bundled_skills_for(["get_assignment_details"]), ["canvas"])
        self.assertEqual(selector._bundled_skills_for(["download_course_file"]), ["canvas"])
        self.assertEqual(selector._bundled_skills_for(["get_course_discussions"]), ["canvas"])
        self.assertEqual(selector._bundled_skills_for(["get_course_modules"]), ["canvas"])


if __name__ == "__main__":
    unittest.main()
