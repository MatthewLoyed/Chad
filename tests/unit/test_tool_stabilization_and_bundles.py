"""Unit tests for tool stabilization and bundle expansion boundaries.

Verifies:
1. Utility tools (write_file, load_skill) do not trigger runaway bundle expansion.
2. Direct domain tools (render_diagram, get_assignment_details) do expand their bundles.
3. stabilize_local_tools NEVER evicts tools from the current turn, even when the
   number of selected tools exceeds the cap.
"""
from collections import OrderedDict
import pytest

from src.infrastructure.tools.selection.turn_selector import TurnToolSelector, NON_TRIGGERING_BUNDLE_TOOLS
from src.core.orchestration.turn.tool_preparation import TurnToolPreparation


class MockRegistry:
    def __init__(self):
        self._bundle_groups = {
            "google_drive": {
                "read_file",
                "list_directory",
                "resolve_path",
                "propose_file_plan",
            },
            "visual_diagram": {
                "load_skill",
                "render_diagram",
                "write_file",
            },
            "visual_typst": {
                "load_skill",
                "compile_typst",
                "write_file",
            },
            "youtube_music": {
                "load_skill",
                "play_youtube_music",
            },
            "canvas": {
                "get_canvas_courses",
                "get_canvas_assignments",
                "get_assignment_details",
                "get_course_files",
                "read_course_file",
            },
        }
        self._bundle_skills = {
            "visual_diagram": "kroki",
            "visual_typst": "typst",
            "youtube_music": "youtube_music",
            "canvas": "canvas",
        }
        self._core_tool_names = ["load_skill", "web_search"]
        self._tools = {}


def test_bundle_expansion_ignores_utility_tools():
    reg = MockRegistry()
    selector = TurnToolSelector(reg)

    # Selecting generic write_file should NOT trigger diagram, typst, music bundles
    expanded = selector._expand_bundles(["write_file", "resolve_path"])
    assert set(expanded) == {"write_file", "resolve_path"}

    # Selecting load_skill should NOT trigger any bundles
    expanded_core = selector._expand_bundles(["load_skill"])
    assert set(expanded_core) == {"load_skill"}

    # Selecting a real domain tool like get_assignment_details SHOULD trigger canvas bundle
    expanded_canvas = selector._expand_bundles(["get_assignment_details", "write_file"])
    assert "get_canvas_courses" in expanded_canvas
    assert "get_canvas_assignments" in expanded_canvas
    assert "get_course_files" in expanded_canvas
    # BUT should NOT have pulled in diagram or typst or youtube_music
    assert "render_diagram" not in expanded_canvas
    assert "compile_typst" not in expanded_canvas
    assert "play_youtube_music" not in expanded_canvas


def test_bundled_skills_for_ignores_utility_tools():
    reg = MockRegistry()
    selector = TurnToolSelector(reg)

    skills = selector._bundled_skills_for(["write_file", "load_skill"])
    assert skills == []

    canvas_skills = selector._bundled_skills_for(["get_assignment_details", "write_file"])
    assert canvas_skills == ["canvas"]


def test_stabilize_local_tools_never_evicts_current_turn():
    prep = TurnToolPreparation(tools_cap=5)
    session_id = "test-session-123"

    # Turn 1: 3 tools
    turn1_schemas = [{"name": "tool_a"}, {"name": "tool_b"}, {"name": "tool_c"}]
    res1 = prep.stabilize_local_tools(session_id, turn1_schemas)
    assert len(res1) == 3
    assert {s["name"] for s in res1} == {"tool_a", "tool_b", "tool_c"}

    # Turn 2: 7 tools (exceeding cap=5!)
    # All 7 current turn tools MUST be preserved, none dropped!
    turn2_schemas = [
        {"name": f"turn2_tool_{i}"} for i in range(7)
    ]
    res2 = prep.stabilize_local_tools(session_id, turn2_schemas)
    assert len(res2) >= 7
    res2_names = {s["name"] for s in res2}
    for i in range(7):
        assert f"turn2_tool_{i}" in res2_names

    # The stale tools from Turn 1 (tool_a, tool_b, tool_c) should have been evicted
    assert "tool_a" not in res2_names
    assert "tool_b" not in res2_names
    assert "tool_c" not in res2_names
