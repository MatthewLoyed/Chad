"""Domain tool groups exposed together by the existing turn selector."""


def default_bundle_groups() -> dict[str, set[str]]:
    """Return fresh groups so one registry cannot mutate another's bundles."""
    return {
        "feed_discovery": {"manage_curiosity", "update_dashboard"},
        "ui_automation": {
            "ui_find", "ui_read", "ui_click", "ui_type", "ui_scroll",
            "window_management", "Snapshot", "Screenshot", "open_app", "open_browser_app"
        },
        "gmail_read": {
            "search_gmail", "read_gmail",
        },
        "gmail_compose": {
            "create_gmail_draft", "draft_gmail_reply",
        },
        "file_system": {
            "list_directory", "read_file", "write_file", "fuzzy_find_local"
        },
        "mcp_gateway": {
            "call_mcp_tool", "list_directory", "read_file",
        },
        "google_drive": {
            "google_drive_search",
            "google_drive_list_folder",
            "google_drive_read_file",
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
        "visual_marp": {
            "load_skill",
            "render_marp",
            "write_file",
        },
        "visual_animation": {
            "load_skill",
            "render_animation",
            "write_file",
        },
        "visual_presenton": {
            "load_skill",
            "generate_presentation",
            "write_file",
        },
        "visual_image": {
            "load_skill",
            "generate_image",
            "write_file",
        },
        "youtube_music": {
            "load_skill",
            "play_youtube_music",
        },
        "places_maps": {
            "load_skill",
            "geocode",
            "search_places",
            "get_route",
            "show_map",
        },
        "canvas": {
            "get_canvas_courses",
            "get_canvas_assignments",
            "get_upcoming_assignments",
            "get_assignment_details",
            "read_course_file",
            "get_course_syllabus",
        },
        "canvas_files": {
            "download_course_file",
            "get_course_files",
        },
        "canvas_modules": {
            "get_course_modules",
            "get_module_items",
            "search_module_items",
            "get_course_pages",
            "read_course_page",
        },
        "canvas_discussions": {
            "get_course_discussions",
            "read_discussion_topic",
        },
        "canvas_quizzes": {
            "get_course_quizzes",
        },
        "canvas_grades": {
            "get_course_grades",
            "get_recent_grades",
            "get_canvas_dashboard_todos",
        },
        "finance": {
            "get_financial_summary",
            "get_recent_transactions",
            "get_investment_portfolio",
            "create_finance_link_token",
            "get_recurring_subscriptions",
            "get_liabilities",
            "get_investment_transactions",
            "refresh_financial_data",
            "render_chart",
        },
    }
