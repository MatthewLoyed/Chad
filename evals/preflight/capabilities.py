r"""
capabilities.py - which external capability does a given tool/case need?

This is the map that makes preflight cheap: running `--suite tool_selection` must not
demand a healthy Gmail token. Adding a tool here automatically pulls its dependency into
every future run that expects that tool.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any, Dict, Iterable, Optional, Set

# -- Capability requirements --------------------------------------------------
#
# Maps an expected tool name to the external capability it needs. Anything not listed
# needs nothing beyond the harness itself (pure-local tools like get_datetime).
#
# Keys are matched exactly first, then by prefix for the dynamic families.

_TOOL_CAPABILITY: Dict[str, str] = {}


def _register(capability: str, *tools: str) -> None:
    for t in tools:
        _TOOL_CAPABILITY[t] = capability


_register(
    "google_oauth",
    # Gmail
    "gmail_check_inbox", "search_gmail", "read_gmail",
    "get_gmail_history", "create_gmail_draft",
    "draft_gmail_reply", "send_email", "refresh_google_auth", "search_contacts",
    # Calendar
    "google_calendar_check",
    # Drive / Docs
    "google_drive_search", "google_drive_read_file", "google_drive_list_folder",
    "google_drive_create_doc", "google_drive_edit_doc",
)
_register(
    "web_search",
    "web_search", "searxng_search", "serper_search", "tavily_search", "playwright_search",
    "search_images",
    # Legacy MCP-era names still present in prod_sampled.jsonl.
    "brave_web_search", "brave_local_search",
)
_register("sandbox", "run_sandboxed_python", "run_sandboxed_bash", "download_sandbox_file",
          "install_package")
_register("browser", "open_browser", "browser_scroll", "refresh_browser_history")
_register("canvas", "get_canvas_courses", "get_canvas_dashboard_todos", "get_course_announcements",
          "get_recent_grades", "get_upcoming_assignments", "get_assignment_details",
          "get_course_files", "get_course_modules", "get_course_pages", "get_course_people",
          "get_course_syllabus", "get_module_items", "search_module_items",
          "read_course_page", "read_course_file", "download_course_file")
_register("telegram", "send_telegram_message")
_register("smart_home", "hubspace_control")
_register("typst", "compile_typst")
_register("marp", "render_marp", "generate_presentation")
_register("docker", "dispatch_coding_task", "check_coding_task", "stop_coding_task")
# Delegation tools are generated per available sub-agent, so they carry their sub-agent's
# external needs. Listed exactly (not by prefix) where the capability differs from the
# generic case; `delegate_to_` below catches any new sibling.
_register("browser", "delegate_to_browser_agent")
_register("web_search", "delegate_to_research_agent", "delegate_to_deep_research_agent")

# Dynamic tool families the main ToolRegistry never lists (sub-agent toolsets and the
# generated delegation tools). Matched by prefix; used both for capability inference and
# to keep `datasets` from flagging them as unknown-tool typos. A value of None means the
# family is real but needs nothing external beyond the harness.
_DYNAMIC_PREFIXES: Dict[str, Optional[str]] = {
    "delegate_to_": None,
    "browser_": "browser",
}

# Sub-agent-internal tools (code_agent's own filesystem toolset). Real, just not in the
# top-level registry.
_SUBAGENT_TOOLS: Set[str] = {
    "grep", "create_file", "search_files", "click", "drag", "scroll", "wait",
    "add_task", "ask_clarification", "create_element", "batch_create_elements",
}


def capability_for_tool(tool: str) -> Optional[str]:
    """Which external capability does calling `tool` require? None = nothing external."""
    if tool in _TOOL_CAPABILITY:
        return _TOOL_CAPABILITY[tool]
    for prefix, cap in _DYNAMIC_PREFIXES.items():
        if tool == prefix or (prefix.endswith("_") and tool.startswith(prefix)):
            return cap
    return None


_EXPECT_TOOL_KEYS = (
    "tools_include", "tools_any", "tools_exclude",
    "scout_tools_include", "scout_tools_any",
    "plan_tools_include", "plan_tools_any",
)


# Lanes in which the model actually EXECUTES the tools it selected. Only these need the
# tools' external dependencies to be healthy.
_EXECUTING_LANES = {"live", "workflow"}


def requirements_for_cases(cases: Iterable[Any], *, lanes: Iterable[str] = ()) -> Set[str]:
    """Derive the capability set the selected cases actually need.

    Two things keep this from over-demanding, which matters more than it sounds: a gate that
    blocks a run it didn't need to block is a gate people start passing --skip-preflight to,
    and then it protects nothing.

    1. Tool-EXECUTION dependencies are only required in a lane that executes tools. The
       dry-run lane just captures which tools got BOUND to the prompt; it never calls them.
       A `tool_selection` case expecting `web_search` to be selected is testing the selector,
       and passes perfectly well with the search backend down.
    2. `tools_exclude` is ignored: a case asserting the model must *not* call a Gmail tool
       does not need a working Gmail token, and demanding one would make negative cases
       (which Anthropic's guidance says to include) artificially expensive.
    """
    caps: Set[str] = {"datasets"}
    lanes = set(lanes)

    if lanes & _EXECUTING_LANES:
        for case in cases:
            expect = getattr(case, "expect", None) or {}
            for key in _EXPECT_TOOL_KEYS:
                if key == "tools_exclude":
                    continue
                for tool in expect.get(key, []) or []:
                    cap = capability_for_tool(tool)
                    if cap:
                        caps.add(cap)

    # Lane-level requirements.
    if lanes & _EXECUTING_LANES:
        caps |= {"server", "auth_token"}
    if "dry-run" in lanes:
        caps.add("lmstudio")          # dry-run still calls the 9B tool selector
    if lanes & (_EXECUTING_LANES | {"dry-run"}):
        caps.add("gate")
    caps.add("disk")
    return caps



_REGISTRY_CACHE: Optional[Set[str]] = None


def _registered_tool_names() -> Optional[Set[str]]:
    """Live tool-registry names, or None if the registry can't be loaded."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    try:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from src.infrastructure.tools.registry import ToolRegistry
            reg = ToolRegistry()
            reg.discover_all()
        _REGISTRY_CACHE = set(reg._tools.keys())
    except Exception:
        return None
    return _REGISTRY_CACHE


def _tool_exists(tool: str, known: Set[str]) -> bool:
    if tool in known or tool in _SUBAGENT_TOOLS:
        return True
    for prefix in _DYNAMIC_PREFIXES:
        if tool == prefix or (prefix.endswith("_") and tool.startswith(prefix)):
            return True
    return False

