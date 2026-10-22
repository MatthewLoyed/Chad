"""Scout operation policy. Unknown tools are denied until explicitly classified."""
from __future__ import annotations
from typing import Any

# Audited native retrieval operations. Mixed memory operations are checked per call.
READ_TOOLS = frozenset({
    "check_app_exists", "web_search", "search_for_tool", "read_file", "list_directory", "read_pdf",
    "manage_user_memory", "search_contacts", "search_gmail", "read_gmail", "get_gmail_history",
    "google_drive_search", "google_drive_list_folder", "google_drive_read_file",
    "get_calendar_events", "read_outlook_emails", "search_outlook_emails",
    "get_datetime", "fuzzy_find_local", "find_project", "search_session_history",
    "search_browser_history", "ui_find", "ui_read", "browser_snapshot",
})
DENIED = frozenset({"call_mcp_tool", "open_app", "window_management", "run_saved_routine", "run_routine",
                    "manage_connectors", "ui_click", "ui_type", "keyboard_control", "mouse_control",
                    # Non-side-effecting means retry-safe, not necessarily an observation.
                    "escalate_to_agent", "switch_to_agent_mode", "switch_to_plan_mode",
                    "dispatch_scout", "skip_scout", "review_scout", "propose_plan",
                    "complete_step", "fail_step", "pause_for_input", "ask_user_question",
                    "execute_approved_plan", "dispatch_coding_task", "load_skill"})


def scout_tool_allowed(name: str, registry: Any = None) -> bool:
    if name in DENIED or name.startswith("delegate_to_"):
        return False
    if name in READ_TOOLS:
        return True
    # Use explicit registered metadata, never naming prefixes or model-supplied claims.
    raw = getattr(registry, "_tools", {}).get(name) if registry is not None else None
    if isinstance(raw, dict):
        annotations = raw.get("annotations") or (raw.get("schema") or {}).get("annotations") or {}
        return annotations.get("readOnlyHint") is True
    if raw is not None and getattr(raw, "is_side_effecting", None) is False:
        return True
    return False
