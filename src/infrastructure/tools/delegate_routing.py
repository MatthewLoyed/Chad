"""Supervisor vs subagent tool routing — coding / research internals via delegates.

Drive and local filesystem tools are bindable on the supervisor for known-target /
quick lookups. Open-ended explore/search-through work still goes through
``delegate_to_traversal_agent`` (see FILE_ROUTING_SUPERVISOR).
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List

# Atomic tools the supervisor must never bind; use handoff tools instead.
SUPERVISOR_DELEGATE_ONLY_TOOLS: FrozenSet[str] = frozenset({
    # code_agent
    "build_html_file",
    "write_file",
    "propose_file_plan",
    "run_sandboxed_python",
    "run_sandboxed_bash",
    "install_package",
    "download_sandbox_file",
    "wait",
    "create_directory",
    "execute_approved_plan",
    # research_agent internals (supervisor uses web_search directly for quick lookups)
    "brave_web_search",
    "fetch_html",
    # find_project is NOT here — shallow coding-roots name lookup for the supervisor.
    # Drive + local FS tools (google_drive_*, fuzzy_find_local, list_directory, read_file)
    # are NOT here — supervisor may call them for known-target / quick lookups.
    # Explore / hop-by-hop / open-ended search-through → delegate_to_traversal_agent
    # (FILE_ROUTING_SUPERVISOR).
    #
    # ui_automation_agent internals (windows-semantic-ui MCP): multi-step, precision-
    # dependent element automation that needs the agent's find-before-click / re-verify
    # grounding protocol. NOT excluded here: `open_app` (local, single-shot launch/resize/
    # switch/move) and `window_management` (list/find/state/simple move-resize) — those are
    # shallow enough for the supervisor to call directly, same as web_search vs
    # delegate_to_research_agent. Without this list, these tools were reachable straight from
    # the supervisor turn whenever the LLM selector picked them (windows-semantic-ui was never
    # added to SUPERVISOR_EXCLUDED_CATEGORIES below, unlike playwright/browser), bypassing
    # ui_automation_agent's find-before-click / re-verify grounding protocol entirely
    # (caught 2026-07-25 auditing supervisor tool selection after reviving the MCP server).
    "ui_find",
    "ui_click",
    "ui_type",
    "ui_read",
    "mouse_control",
    "keyboard_control",
    "screenshot_control",
    "file_save",
})

SUPERVISOR_EXCLUDED_CATEGORIES: tuple[str, ...] = (
    "notion",
    "gmail",
    "browser",
    "playwright",
    "coding",
    "canvas",
    "canva",
    "excalidraw",
    "github",
    "todoist",
    "fetch",
    "supabase",
    "spotify",
    "mcp",
)

# Subagent handling each excluded category. If the subagent is disabled,
# its tools are not excluded from the supervisor, preventing black-holing
# where neither the subagent nor the supervisor can run them.
CATEGORY_TO_DELEGATE_AGENT: Dict[str, str] = {
    "browser": "browser_agent",
    "playwright": "browser_agent",
    "coding": "code_agent",
    "canvas": "canvas_agent",
}

# Planner step tool names → supervisor handoff tool to call.
# Drive / local FS / find_project atoms are intentionally omitted: the planner may name
# them for quick steps, or name delegate_to_traversal_agent / code_agent for heavier work.
STEP_TOOL_TO_DELEGATE: Dict[str, str] = {
    "build_html_file": "delegate_to_code_agent",
    "write_file": "delegate_to_code_agent",
    "propose_file_plan": "delegate_to_code_agent",
    "run_sandboxed_python": "delegate_to_code_agent",
    "run_sandboxed_bash": "delegate_to_code_agent",
    "create_directory": "delegate_to_code_agent",
    "brave_web_search": "parallel_research",
    "fetch_html": "parallel_research",
    # Canvas LMS tools map to canvas_agent
    "get_canvas_courses": "delegate_to_canvas_agent",
    "get_course_modules": "delegate_to_canvas_agent",
    "get_module_items": "delegate_to_canvas_agent",
    "search_module_items": "delegate_to_canvas_agent",
    "get_canvas_assignments": "delegate_to_canvas_agent",
    "get_upcoming_assignments": "delegate_to_canvas_agent",
    "get_assignment_details": "delegate_to_canvas_agent",
    "get_assignment_submission": "delegate_to_canvas_agent",
    "get_course_files": "delegate_to_canvas_agent",
    "read_course_file": "delegate_to_canvas_agent",
    "download_course_file": "delegate_to_canvas_agent",
    "get_course_grades": "delegate_to_canvas_agent",
    "get_recent_grades": "delegate_to_canvas_agent",
    "get_course_syllabus": "delegate_to_canvas_agent",
    "get_course_pages": "delegate_to_canvas_agent",
    "read_course_page": "delegate_to_canvas_agent",
    "get_course_discussions": "delegate_to_canvas_agent",
    "read_discussion_topic": "delegate_to_canvas_agent",
    "get_course_quizzes": "delegate_to_canvas_agent",
    "get_canvas_calendar_events": "delegate_to_canvas_agent",
    "get_canvas_dashboard_todos": "delegate_to_canvas_agent",
    "get_course_people": "delegate_to_canvas_agent",
    "get_canvas_activity_stream": "delegate_to_canvas_agent",
    # Browser driving tools map to browser_agent
    "browser_navigate": "delegate_to_browser_agent",
    "browser_navigate_back": "delegate_to_browser_agent",
    "browser_snapshot": "delegate_to_browser_agent",
    "browser_click": "delegate_to_browser_agent",
    "browser_type": "delegate_to_browser_agent",
    "browser_scroll": "delegate_to_browser_agent",
    "browser_capture_text": "delegate_to_browser_agent",
    "browser_tabs": "delegate_to_browser_agent",
    "open_browser": "delegate_to_browser_agent",
}


def is_supervisor_delegate_only(
    name: str,
    categories: Dict[str, List[str]],
    tool_sources: Dict[str, str],
    disabled_subagents: Optional[Iterable[str]] = None,
) -> bool:
    if name in SUPERVISOR_DELEGATE_ONLY_TOOLS:
        return True

    if disabled_subagents is None:
        try:
            from src.shared.config_manager import ConfigManager
            disabled = set(ConfigManager.get("disabled_subagents") or [])
        except Exception:
            disabled = set()
    else:
        disabled = set(disabled_subagents)

    # When browser_agent is active, all atomic browser driving tools and open_browser
    # must be delegated to browser_agent to prevent context bloat and supervisor ping-pong.
    if "browser_agent" not in disabled:
        if name.startswith("browser_") and name not in ("search_browser_history", "refresh_browser_history"):
            return True
        if name == "open_browser":
            return True

    # Categories whose handling subagent is currently disabled should not be excluded
    # from the supervisor (e.g. browser_agent off -> supervisor can bind browser tools directly).
    active_excluded = [
        cat for cat in SUPERVISOR_EXCLUDED_CATEGORIES
        if CATEGORY_TO_DELEGATE_AGENT.get(cat) not in disabled
    ]

    if any(name in categories.get(cat, []) for cat in active_excluded):
        return True
    if tool_sources.get(name) in active_excluded:
        return True
    return False


def delegate_hints_for_step_tools(step_tools: List[str]) -> List[str]:
    """Map atomic plan-step tool refs to handoff tools the supervisor should call."""
    hints: List[str] = []
    for ref in step_tools:
        delegate = STEP_TOOL_TO_DELEGATE.get(ref)
        if delegate and delegate not in hints:
            hints.append(delegate)
    return hints
