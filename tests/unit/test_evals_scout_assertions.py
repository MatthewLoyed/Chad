"""Unit tests for scout→planner eval assertions."""
from evals.core import check_live_assertions, plan_tool_names, scout_tools_from_timeline


class _Trace:
    def __init__(self, **kwargs):
        self.tool_names = kwargs.get("tool_names", [])
        self.plan = kwargs.get("plan")
        self.timeline = kwargs.get("timeline", [])
        self.gather_brief = kwargs.get("gather_brief")
        self.final_response = kwargs.get("final_response", "")


def test_scout_tools_from_timeline_excludes_scout_gather_and_post_plan():
    timeline = [
        "reasoning:Scout",
        "tool:scout_gather",
        "tool:read_file",
        "tool:list_directory",
        "plan",
        "tool:write_file",
    ]
    assert scout_tools_from_timeline(timeline) == ["read_file", "list_directory"]


def test_plan_tool_names_collects_step_tools():
    plan = [
        {"id": "step_1", "tools": ["read_file"], "description": "read"},
        {"id": "step_2", "tools": ["write_file"], "description": "write"},
    ]
    assert plan_tool_names(plan) == {"read_file", "write_file"}


def test_scout_tools_exclude_fails_on_drive_creep():
    trace = _Trace(
        timeline=["tool:scout_gather", "tool:google_drive_search", "plan"],
        plan=[{"id": "step_1", "tools": ["write_file"], "description": "x"}],
        gather_brief="Found README at ./README.md",
    )
    fails = check_live_assertions(
        {"scout_tools_exclude": ["google_drive_search"], "gather_brief_present": True},
        trace,
    )
    assert any("scout_tools_exclude" in f for f in fails)


def test_plan_tools_any_requires_write_step():
    trace = _Trace(
        timeline=["tool:scout_gather", "tool:read_file", "plan"],
        plan=[{"id": "step_1", "tools": ["read_file"], "description": "read again"}],
        gather_brief="README path confirmed.",
    )
    fails = check_live_assertions({"plan_tools_any": ["write_file"]}, trace)
    assert any("plan_tools_any" in f for f in fails)
