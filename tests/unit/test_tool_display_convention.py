"""Tests for the tool display formatter convention."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tools.base_tool import BaseTool
from src.tools.system.switch_to_plan_mode import SwitchToPlanModeTool
from src.tools.system.kanban_board_tool import KanbanBoardTool


def test_base_tool_default_has_no_custom_formatter():
    class BareTool(BaseTool):
        name = "bare_tool"

        def get_schema(self):
            return {"name": self.name, "description": "x", "parameters": {"type": "object", "properties": {}}}

        async def execute(self, **kwargs):
            return {"success": True}

    assert BareTool.has_display_formatter() is False
    assert BareTool().format_action({}) == "Running Bare Tool"


def test_format_action_override_counts_as_custom():
    class LabeledTool(BaseTool):
        name = "labeled_tool"

        def format_action(self, params):
            return "Doing the thing"

        def get_schema(self):
            return {"name": self.name, "description": "x", "parameters": {"type": "object", "properties": {}}}

        async def execute(self, **kwargs):
            return {"success": True}

    assert LabeledTool.has_display_formatter() is True


def test_known_tools_have_display_formatters():
    assert SwitchToPlanModeTool.has_display_formatter()
    assert KanbanBoardTool.has_display_formatter()
