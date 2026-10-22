"""Telegram pocket-remote agent — fixed tools and prompt constants.

Separate from the desktop supervisor: no specialist delegates, no GUI cards.
Contact desk escalates here (category=telegram_agent), not to full agent.
"""

from __future__ import annotations

# Fixed tool menu for telegram_agent turns (order is display-only).
TELEGRAM_AGENT_TOOL_NAMES: tuple[str, ...] = (
    "web_search",
    "search_gmail",
    "read_gmail",
    "send_email",
    "draft_gmail_reply",
    "create_gmail_draft",
    "search_contacts",
    "google_calendar_check",
    "manage_user_memory",
    "manage_curiosity",
    "stay_silent",
    "geocode",
    "search_places",
    "get_route",
    "compile_typst",
    "render_diagram",
    "render_marp",
    "generate_presentation",
    "generate_image",
    "render_chart",
    "render_animation",
    "set_reminder",
    "get_datetime",
)


