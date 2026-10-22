"""Example buddy rules — copy patterns into startup hooks or plugins."""

from __future__ import annotations

from src.application.events import ToolCompleted
from src.buddy import BuddyRule

# Register on BuddyEngine at Brain boot:
#   engine.register_rule(EMAIL_SENT_RULE)

EMAIL_SENT_RULE = BuddyRule(
    name="email_sent",
    events=(ToolCompleted,),
    match=lambda e: e.success and e.tool_name in ("send_email", "gmail_send", "gmail_send_message"),
    cue="success",
    cue_duration_s=2.0,
    meta_from_event=lambda e: {"tool": e.tool_name},
    priority=50,
)
