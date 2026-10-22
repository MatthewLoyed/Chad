"""Supervisor file-tool binding: Drive + local FS quick path vs coding still delegated."""
from __future__ import annotations

from src.infrastructure.tools.delegate_routing import (
    STEP_TOOL_TO_DELEGATE,
    SUPERVISOR_DELEGATE_ONLY_TOOLS,
    SUPERVISOR_EXCLUDED_CATEGORIES,
    delegate_hints_for_step_tools,
    is_supervisor_delegate_only,
)


def test_google_drive_not_excluded_category():
    assert "google-drive" not in SUPERVISOR_EXCLUDED_CATEGORIES


def test_local_fs_atoms_not_delegate_only_by_name():
    for name in ("fuzzy_find_local", "list_directory", "read_file", "find_project"):
        assert name not in SUPERVISOR_DELEGATE_ONLY_TOOLS


def test_drive_and_local_fs_bindable_on_supervisor():
    categories = {
        "google-drive": [
            "google_drive_search",
            "google_drive_list_folder",
            "google_drive_read_file",
        ],
        "filesystem": ["list_directory", "read_file", "fuzzy_find_local"],
        "os": ["find_project"],
        "coding": ["write_file", "run_sandboxed_python", "build_html_file"],
    }
    sources = {
        "google_drive_read_file": "google-drive",
        "read_file": "filesystem",
        "fuzzy_find_local": "filesystem",
        "list_directory": "filesystem",
        "find_project": "os",
        "write_file": "coding",
        "build_html_file": "coding",
    }
    for name in (
        "google_drive_search",
        "google_drive_list_folder",
        "google_drive_read_file",
        "fuzzy_find_local",
        "list_directory",
        "read_file",
        "find_project",
    ):
        assert not is_supervisor_delegate_only(name, categories, sources), name

    assert is_supervisor_delegate_only("write_file", categories, sources)
    assert is_supervisor_delegate_only("run_sandboxed_python", categories, sources)
    assert is_supervisor_delegate_only("build_html_file", categories, sources)


def test_step_tool_remap_skips_file_atoms():
    for name in (
        "google_drive_search",
        "google_drive_list_folder",
        "google_drive_read_file",
        "fuzzy_find_local",
        "list_directory",
        "read_file",
        "find_project",
    ):
        assert name not in STEP_TOOL_TO_DELEGATE

    hints = delegate_hints_for_step_tools(
        ["google_drive_read_file", "read_file", "find_project", "write_file", "build_html_file"]
    )
    assert hints == ["delegate_to_code_agent"]


def test_browser_tools_not_excluded_when_browser_agent_disabled():
    categories = {"browser": ["browser_navigate", "browser_click"]}
    sources = {"browser_navigate": "playwright", "browser_click": "playwright"}

    # When browser_agent is disabled, browser tools are NOT excluded to avoid black hole
    assert not is_supervisor_delegate_only(
        "browser_navigate", categories, sources, disabled_subagents=["browser_agent"]
    )
    assert not is_supervisor_delegate_only(
        "browser_click", categories, sources, disabled_subagents=["browser_agent"]
    )
    assert not is_supervisor_delegate_only(
        "open_browser", categories, sources, disabled_subagents=["browser_agent"]
    )

    # When browser_agent is enabled, browser tools ARE excluded to force delegation
    assert is_supervisor_delegate_only(
        "browser_navigate", categories, sources, disabled_subagents=[]
    )
    assert is_supervisor_delegate_only(
        "browser_click", categories, sources, disabled_subagents=[]
    )
    assert is_supervisor_delegate_only(
        "browser_snapshot", categories, sources, disabled_subagents=[]
    )
    assert is_supervisor_delegate_only(
        "browser_scroll", categories, sources, disabled_subagents=[]
    )
    assert is_supervisor_delegate_only(
        "open_browser", categories, sources, disabled_subagents=[]
    )

    # Browser history tools are query tools, NOT driving tools, so they are not excluded
    assert not is_supervisor_delegate_only(
        "search_browser_history", categories, sources, disabled_subagents=[]
    )
    assert not is_supervisor_delegate_only(
        "refresh_browser_history", categories, sources, disabled_subagents=[]
    )


def test_browser_step_tools_remap_to_browser_agent():
    for name in ("browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_scroll", "open_browser"):
        assert STEP_TOOL_TO_DELEGATE.get(name) == "delegate_to_browser_agent"

    hints = delegate_hints_for_step_tools(
        ["browser_navigate", "browser_click", "write_file"]
    )
    assert hints == ["delegate_to_browser_agent", "delegate_to_code_agent"]