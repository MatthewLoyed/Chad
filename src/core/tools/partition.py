from enum import Enum
from typing import Dict

class ToolPartition(Enum):
    LOCAL = "local"     # Must run on the physical PC (Hardware/OS access)
    CLOUD = "cloud"     # Can run on the server (APIs, Scrapers, Code Execution)
    HYBRID = "hybrid"   # Can run on either, default to Cloud for latency

# A manifest defining where each tool should live.
# If a tool is not listed, it defaults to CLOUD (safe for API-based tools).
# Only tools that require local hardware or filesystem access should be LOCAL.
TOOL_MANIFEST: Dict[str, ToolPartition] = {
    # ── LOCAL: Hardware / OS / Local Filesystem ──────────────────────────────
    # Media hardware
    "control_volume": ToolPartition.LOCAL,
    "capture_screen": ToolPartition.LOCAL,
    "youtube_desktop_control": ToolPartition.LOCAL,
    "play_youtube_music": ToolPartition.LOCAL,  # opens music.youtube.com in the local browser
    # OS / process control
    "App": ToolPartition.LOCAL, # Consolidated local app tool
    "check_app_exists": ToolPartition.LOCAL,
    "wait": ToolPartition.LOCAL,
    "run_host_command": ToolPartition.LOCAL,
    "run_script": ToolPartition.LOCAL,
    "typewriter": ToolPartition.LOCAL,
    "resolve_path": ToolPartition.LOCAL,
    # Windows Desktop MCP Tools (Hardwired to PC)
    "Snapshot": ToolPartition.LOCAL,
    "Screenshot": ToolPartition.LOCAL,
    "Click": ToolPartition.LOCAL,
    "MultiSelect": ToolPartition.LOCAL,
    "Hover": ToolPartition.LOCAL,
    "Drag": ToolPartition.LOCAL,
    "Type": ToolPartition.LOCAL,
    "Keyboard": ToolPartition.LOCAL,
    # Local filesystem
    "list_directory": ToolPartition.LOCAL,
    "fuzzy_find_local": ToolPartition.LOCAL,
    "read_file": ToolPartition.LOCAL,
    "file_manager": ToolPartition.LOCAL,
    "write_file": ToolPartition.LOCAL,
    "propose_file_plan": ToolPartition.LOCAL,
    "execute_approved_plan": ToolPartition.LOCAL,
    "open_file": ToolPartition.LOCAL,
    # Opens a browser window on the local machine
    "open_browser": ToolPartition.LOCAL,
    # Persona & Knowledge (Chad/ folder)
    # Persona & Knowledge (Chad/ folder & Cloud)
    "manage_user_memory": ToolPartition.LOCAL,
    "manage_persona_rules": ToolPartition.CLOUD,
    "manage_heartbeat": ToolPartition.LOCAL,
    "manage_curiosity": ToolPartition.LOCAL,

    # ── CLOUD: Web / API / Data (no local hardware needed) ───────────────────
    # Web search
    "search_web_brave": ToolPartition.CLOUD,
    "search_web": ToolPartition.CLOUD,
    # Gmail
    "search_gmail": ToolPartition.CLOUD,
    "read_gmail": ToolPartition.CLOUD,
    "draft_gmail_reply": ToolPartition.CLOUD,
    "send_email": ToolPartition.CLOUD,
    "create_gmail_draft": ToolPartition.CLOUD,
    # Google Calendar
    "get_calendar_events": ToolPartition.CLOUD,
    "add_calendar_event": ToolPartition.CLOUD,
    "update_calendar_event": ToolPartition.CLOUD,
    "delete_calendar_event": ToolPartition.CLOUD,
    # Google Drive & Docs (native tools; niche MCP tools still available)
    "google_drive_list_folder": ToolPartition.CLOUD,
    "google_drive_search": ToolPartition.CLOUD,
    "google_drive_read_file": ToolPartition.CLOUD,
    "google_drive_create_doc": ToolPartition.CLOUD,
    "google_drive_edit_doc": ToolPartition.CLOUD,
    # Google Contacts
    "search_contacts": ToolPartition.CLOUD,
    # Canvas LMS
    "get_canvas_courses": ToolPartition.CLOUD,
    "get_course_modules": ToolPartition.CLOUD,
    "get_module_items": ToolPartition.CLOUD,
    "search_module_items": ToolPartition.CLOUD,
    "get_course_announcements": ToolPartition.CLOUD,
    "get_course_files": ToolPartition.CLOUD,
    "read_course_file": ToolPartition.CLOUD,
    "get_course_syllabus": ToolPartition.CLOUD,
    "get_course_pages": ToolPartition.CLOUD,
    "read_course_page": ToolPartition.CLOUD,
    "get_course_people": ToolPartition.CLOUD,
    "get_upcoming_assignments": ToolPartition.CLOUD,
    "get_recent_grades": ToolPartition.CLOUD,
    "get_assignment_details": ToolPartition.CLOUD,
    "get_canvas_dashboard_todos": ToolPartition.CLOUD,
    # Todoist
    "get_tasks": ToolPartition.CLOUD,
    "get_daily_tasks": ToolPartition.CLOUD,
    "get_todoist_projects": ToolPartition.CLOUD,
    "get_project_tasks": ToolPartition.CLOUD,
    "complete_todoist_task": ToolPartition.CLOUD,
    "breakdown_task": ToolPartition.CLOUD,
    "add_research_notes": ToolPartition.CLOUD,
    "enrich_task": ToolPartition.CLOUD,
    # Monitoring
    "gmail_check_inbox": ToolPartition.CLOUD,
    "google_calendar_check": ToolPartition.CLOUD,
    "twitter_check_user": ToolPartition.CLOUD,
    "youtube_check_channel": ToolPartition.CLOUD,
    # Time (cron/DB-backed, no audio hardware dependency)
    "get_datetime": ToolPartition.CLOUD,
    "schedule_reminder": ToolPartition.CLOUD,
    "reminder": ToolPartition.CLOUD,
    "alarm": ToolPartition.CLOUD,
    "timer": ToolPartition.CLOUD,
    # Messaging
    "send_telegram_message": ToolPartition.CLOUD,
    # Smart home (Hubspace API - cloud-based, not local hardware)
    "hubspace_control": ToolPartition.CLOUD,
    # System / meta
    "manage_heartbeat": ToolPartition.CLOUD,
    "ask_user_question": ToolPartition.CLOUD,
    "self_control": ToolPartition.CLOUD,
    "test_toolkit": ToolPartition.CLOUD,
}
