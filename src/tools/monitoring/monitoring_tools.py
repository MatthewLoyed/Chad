"""
Monitoring Tools for Heartbeat Checks

These tools allow the AI to check external services during
autonomous heartbeat cycles.
"""

from typing import Dict, Any
from src.tools.base_tool import BaseTool


class GmailCheckInboxTool(BaseTool):
    """Check Gmail for unread emails matching criteria."""

    name = "gmail_check_inbox"
    description = (
        "For autonomous heartbeat/monitoring setup ONLY — one keyword-filtered unread-email "
        "check, no thread/body detail. For an interactive 'check my email' request right now, "
        "use search_gmail instead."
    )
    category = "system"

    def get_schema(self) -> Dict[str, Any]:
        """Return tool schema for AI model."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "description": "Keywords to search for in subject/body",
                        "items": {"type": "string"}
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum emails to return",
                        "default": 5
                    }
                },
                "required": []
            }
        }

    async def execute(self, keywords: list = None, max_results: int = 5, **kwargs) -> Dict[str, Any]:
        """
        Check Gmail for unread emails.

        Returns list of emails with: subject, sender, snippet, timestamp
        """
        try:
            # Use the real Gmail tool
            from src.tools.workspace.gmail import SearchGmailTool

            gmail_tool = SearchGmailTool()

            # Build query based on keywords
            query_parts = []
            if keywords:
                # Search for keywords in subject or body
                keyword_query = " OR ".join([f"({kw})" for kw in keywords])
                query_parts.append(f"({keyword_query})")

            query = " ".join(query_parts) if query_parts else None

            # Get unread emails
            result = await gmail_tool.execute(
                query=query,
                unread_only=True,
                limit=max_results
            )

            if not result.get("success"):
                return {
                    "unread_count": 0,
                    "important_emails": [],
                    "message": result.get("error", "Failed to check Gmail")
                }

            emails = result.get("emails", [])

            # Format for heartbeat
            important_emails = []
            for email in emails:
                important_emails.append({
                    "subject": email.get("subject", "No subject"),
                    "sender": email.get("from", "Unknown sender"),
                    "snippet": email.get("snippet", "")[:100],
                    "date": email.get("date", "Unknown date")
                })

            return {
                "unread_count": len(important_emails),
                "important_emails": important_emails,
                "message": f"Found {len(important_emails)} unread emails" if important_emails else "No new important emails"
            }

        except Exception as e:
            return {
                "unread_count": 0,
                "important_emails": [],
                "message": f"Gmail check failed: {str(e)}"
            }


class TwitterCheckUserTool(BaseTool):
    """Check if a Twitter user posted recently."""

    name = "twitter_check_user"
    description = (
        "For autonomous heartbeat/monitoring setup ONLY — polls whether one Twitter/X "
        "username posted within the last N minutes. Not implemented for one-off lookups; "
        "for reading someone's recent posts right now, use web_search instead."
    )
    category = "system"

    def get_schema(self) -> Dict[str, Any]:
        """Return tool schema for AI model."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "Twitter username to check"
                    },
                    "minutes_ago": {
                        "type": "integer",
                        "description": "Check posts from last N minutes",
                        "default": 30
                    }
                },
                "required": ["username"]
            }
        }

    async def execute(self, username: str = "Dream", minutes_ago: int = 30, **kwargs) -> Dict[str, Any]:
        """
        Check Twitter for recent posts.

        Returns list of tweets with: text, url, timestamp
        """
        # TODO: Implement Twitter API integration
        return {
            "new_posts": False,
            "posts": [],
            "message": "Twitter integration not yet implemented. Add Twitter API credentials."
        }


class YoutubeCheckChannelTool(BaseTool):
    """Check if a YouTube channel uploaded recently."""

    name = "youtube_check_channel"
    description = (
        "For autonomous heartbeat/monitoring setup ONLY — polls whether one YouTube channel "
        "uploaded within the last N minutes. Not implemented for one-off lookups; to find or "
        "watch a channel's recent videos right now, use web_search instead."
    )
    category = "system"

    def get_schema(self) -> Dict[str, Any]:
        """Return tool schema for AI model."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "YouTube channel ID or username"
                    },
                    "minutes_ago": {
                        "type": "integer",
                        "description": "Check uploads from last N minutes",
                        "default": 30
                    }
                },
                "required": ["channel_id"]
            }
        }

    async def execute(self, channel_id: str = "Dream", minutes_ago: int = 30, **kwargs) -> Dict[str, Any]:
        """
        Check YouTube for recent uploads.

        Returns list of videos with: title, url, published_at
        """
        # TODO: Implement YouTube Data API integration
        return {
            "new_videos": False,
            "videos": [],
            "message": "YouTube integration not yet implemented. Add YouTube Data API key."
        }


class GoogleCalendarCheckTool(BaseTool):
    """Check Google Calendar for upcoming events."""

    name = "google_calendar_check"
    description = (
        "For autonomous heartbeat/monitoring setup ONLY — polls for events in the next N "
        "hours as part of a periodic check. Not for an interactive 'what's on my calendar' "
        "request — this tool has no ad-hoc query parameters."
    )
    category = "system"

    def get_schema(self) -> Dict[str, Any]:
        """Return tool schema for AI model."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_ahead": {
                        "type": "integer",
                        "description": "Look ahead N hours",
                        "default": 2
                    }
                },
                "required": []
            }
        }

    async def execute(self, hours_ahead: int = 2, **kwargs) -> Dict[str, Any]:
        """
        Check calendar for upcoming events.

        Returns list of events with: title, start_time, location
        """
        try:
            # Use the real Calendar tool
            from src.tools.workspace.calendar import GetCalendarEventsTool

            calendar_tool = GetCalendarEventsTool()

            # Get upcoming events filtered by hours_ahead
            result = await calendar_tool.execute(limit=10, hours_ahead=hours_ahead)

            if not result.get("success"):
                return {
                    "upcoming_events": [],
                    "message": result.get("error", "Failed to check calendar")
                }

            events = result.get("events", [])

            # Format for heartbeat
            upcoming_events = []
            for event in events:
                upcoming_events.append({
                    "title": event.get("summary", "No title"),
                    "start_time": event.get("start_formatted", event.get("start", "Unknown time")),
                    "relative_time": event.get("relative_time", ""),
                    "starts_in_minutes": event.get("starts_in_minutes"),
                    "location": event.get("location", "No location"),
                    "link": event.get("link", "")
                })

            return {
                "upcoming_events": upcoming_events,
                "message": f"Found {len(upcoming_events)} upcoming event(s) in next {hours_ahead} hours" if upcoming_events else "No upcoming events"
            }

        except Exception as e:
            return {
                "upcoming_events": [],
                "message": f"Calendar check failed: {str(e)}"
            }
