"""
Google Workspace Calendar Tools
Provides modern tools for querying, scheduling, updating, and deleting Google Calendar events.
Also provides upcoming schedule context for proactive assistant awareness.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from googleapiclient.errors import HttpError

from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display
from src.shared.logger import setup_logger
from src.tools.workspace.manager import GoogleWorkspaceManager

logger = setup_logger("GoogleWorkspaceCalendar")

# In-memory cache for schedule context to avoid redundant Google API calls on rapid turns
_SCHEDULE_CACHE: Dict[str, Any] = {
    "timestamp": 0.0,
    "context": "",
    "lookahead_hours": 0,
}
_CACHE_TTL_SECONDS = 120.0  # 2 minutes


def _get_local_timezone():
    """Get local timezone safely."""
    return datetime.now().astimezone().tzinfo


def _format_relative_time(dt: datetime, now: datetime) -> str:
    """Format difference between dt and now into human-friendly relative time."""
    diff = dt - now
    total_minutes = int(diff.total_seconds() // 60)

    if total_minutes < 0:
        past_minutes = abs(total_minutes)
        if past_minutes < 60:
            return f"started {past_minutes}m ago"
        hours = past_minutes // 60
        mins = past_minutes % 60
        return f"started {hours}h {mins}m ago"
    elif total_minutes == 0:
        return "starting right now"
    elif total_minutes < 60:
        return f"starts in {total_minutes} mins"
    elif total_minutes < 120:
        mins = total_minutes % 60
        return f"starts in 1 hr {mins}m" if mins else "starts in 1 hr"
    else:
        hours = total_minutes // 60
        mins = total_minutes % 60
        return f"starts in {hours} hrs {mins}m" if mins else f"starts in {hours} hrs"


def parse_datetime_flexible(time_str: str, default_duration_minutes: int = 60) -> tuple[datetime, datetime]:
    """
    Parse a start time string (ISO or natural language) and return (start_dt, end_dt)
    with timezone information.
    """
    local_tz = _get_local_timezone()
    now_local = datetime.now(local_tz)

    start_dt: Optional[datetime] = None

    # Try ISO formats
    try:
        clean_str = time_str.strip()
        if clean_str.endswith('Z'):
            start_dt = datetime.fromisoformat(clean_str[:-1] + '+00:00').astimezone(local_tz)
        else:
            start_dt = datetime.fromisoformat(clean_str)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=local_tz)
    except (ValueError, TypeError):
        pass

    # Basic natural language parsing fallback
    if start_dt is None:
        low = time_str.lower().strip()
        base_date = now_local

        if 'tomorrow' in low:
            base_date = now_local + timedelta(days=1)
        elif 'next week' in low:
            base_date = now_local + timedelta(weeks=1)

        import re
        time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', low)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2)) if time_match.group(2) else 0
            meridiem = time_match.group(3)

            if meridiem == 'pm' and hour < 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0

            start_dt = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            # Default to next full hour from now
            start_dt = now_local + timedelta(hours=1)

    end_dt = start_dt + timedelta(minutes=default_duration_minutes)
    return start_dt, end_dt


@tool_display("Retrieving calendar events")
class GetCalendarEventsTool(BaseTool):
    """Tool to fetch upcoming calendar events."""

    name = "get_calendar_events"
    description = (
        "Get upcoming events from Google Calendar. Can filter by keyword/search query, "
        "lookahead window (hours_ahead), or specific time boundaries. Returns event summaries, "
        "start/end times, locations, and relative time (e.g. 'starts in 15 mins')."
    )
    category = "calendar"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default: 10)",
                        "default": 10
                    },
                    "search_query": {
                        "type": "string",
                        "description": "Optional keyword or title to filter events by (e.g., 'Class', 'Meeting', 'Project')"
                    },
                    "hours_ahead": {
                        "type": "integer",
                        "description": "Optional lookahead window in hours from now (e.g., 2, 24, 48)"
                    },
                    "time_min": {
                        "type": "string",
                        "description": "Optional start boundary in ISO format (defaults to current time)"
                    },
                    "time_max": {
                        "type": "string",
                        "description": "Optional end boundary in ISO format"
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID to query (default: 'primary')",
                        "default": "primary"
                    }
                },
                "required": []
            }
        }

    async def execute(
        self,
        limit: int = 10,
        search_query: Optional[str] = None,
        hours_ahead: Optional[int] = None,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        calendar_id: str = "primary",
        **kwargs
    ) -> Dict[str, Any]:
        try:
            service = self._manager.get_calendar_service()
            if not service:
                return {
                    "success": False,
                    "error": "Failed to connect to Google Calendar. Please ensure credentials exist and authentication is complete."
                }

            now_utc = datetime.now(timezone.utc)
            local_tz = _get_local_timezone()
            now_local = datetime.now(local_tz)

            # Resolve time_min
            if time_min:
                resolved_min = time_min if time_min.endswith('Z') or '+' in time_min else time_min + 'Z'
            else:
                resolved_min = now_utc.isoformat()

            # Resolve time_max
            resolved_max: Optional[str] = None
            if time_max:
                resolved_max = time_max if time_max.endswith('Z') or '+' in time_max else time_max + 'Z'
            elif hours_ahead is not None and hours_ahead > 0:
                resolved_max = (now_utc + timedelta(hours=hours_ahead)).isoformat()

            try:
                limit = int(limit) if limit is not None else 10
            except (ValueError, TypeError):
                limit = 10
            limit = max(1, min(limit, 100))

            request_params: Dict[str, Any] = {
                'calendarId': calendar_id,
                'timeMin': resolved_min,
                'maxResults': min(limit, 50),
                'singleEvents': True,
                'orderBy': 'startTime',
            }
            if resolved_max:
                request_params['timeMax'] = resolved_max
            if search_query:
                request_params['q'] = search_query

            events_result = service.events().list(**request_params).execute()
            items = events_result.get('items', [])

            formatted_events = []
            for item in items:
                start_raw = item['start'].get('dateTime', item['start'].get('date'))
                end_raw = item.get('end', {}).get('dateTime', item.get('end', {}).get('date'))

                # Calculate relative time and formatted display
                relative_time = "all day"
                start_formatted = start_raw
                end_formatted = end_raw or ""
                starts_in_minutes: Optional[int] = None

                if 'T' in (start_raw or ''):
                    try:
                        clean_start = start_raw.replace('Z', '+00:00')
                        start_dt = datetime.fromisoformat(clean_start).astimezone(local_tz)
                        starts_in_minutes = int((start_dt - now_local).total_seconds() // 60)
                        relative_time = _format_relative_time(start_dt, now_local)
                        start_formatted = start_dt.strftime('%A, %b %d at %I:%M %p')
                        if end_raw and 'T' in end_raw:
                            clean_end = end_raw.replace('Z', '+00:00')
                            end_dt = datetime.fromisoformat(clean_end).astimezone(local_tz)
                            end_formatted = end_dt.strftime('%I:%M %p')
                    except Exception as parse_err:
                        logger.debug(f"Could not parse event date: {parse_err}")

                formatted_events.append({
                    "id": item.get('id', ''),
                    "summary": item.get('summary', 'Untitled event'),
                    "start": start_raw,
                    "start_formatted": start_formatted,
                    "end_formatted": end_formatted,
                    "relative_time": relative_time,
                    "starts_in_minutes": starts_in_minutes,
                    "location": item.get('location', ''),
                    "description": (item.get('description') or '')[:200],
                    "link": item.get('htmlLink', ''),
                })

            query_note = f" matching '{search_query}'" if search_query else ""
            return {
                "success": True,
                "result": f"Found {len(formatted_events)} upcoming event(s){query_note}.",
                "count": len(formatted_events),
                "events": formatted_events,
            }

        except PermissionError as e:
            logger.warning(f"Google Calendar auth error: {e}")
            return {
                "success": False,
                "error": f"Authentication required: {e}. Call 'refresh_google_auth' to re-authenticate."
            }
        except HttpError as e:
            logger.error(f"Calendar API error: {e}", exc_info=True)
            return {"success": False, "error": f"Google Calendar API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to fetch calendar events: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@tool_display("Adding a calendar event")
class AddCalendarEventTool(BaseTool):
    """Tool to create events in Google Calendar."""

    name = "add_calendar_event"
    description = (
        "Create a new event in Google Calendar. Specify the summary/title, start time, "
        "and optional duration or end time, location, and description."
    )
    category = "calendar"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Title/name of the event (e.g. 'Software Engineering Class', 'Dentist Appointment')"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time in ISO format (e.g. '2026-10-13T14:30:00') or natural language (e.g. 'tomorrow at 3pm')"
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Duration in minutes (default: 60)",
                        "default": 60
                    },
                    "end_time": {
                        "type": "string",
                        "description": "Optional explicit end time in ISO format (overrides duration_minutes)"
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional location (e.g. 'Brock Hall 204' or Zoom URL)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional notes or details for the event"
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar to add event to (default: 'primary')",
                        "default": "primary"
                    }
                },
                "required": ["summary", "start_time"]
            }
        }

    async def execute(
        self,
        summary: str,
        start_time: str,
        duration_minutes: int = 60,
        end_time: Optional[str] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
        calendar_id: str = "primary",
        **kwargs
    ) -> Dict[str, Any]:
        try:
            service = self._manager.get_calendar_service()
            if not service:
                return {"success": False, "error": "Failed to connect to Google Calendar."}

            start_dt, end_dt = parse_datetime_flexible(start_time, default_duration_minutes=duration_minutes)
            if end_time:
                explicit_end, _ = parse_datetime_flexible(end_time)
                if explicit_end > start_dt:
                    end_dt = explicit_end

            event_body = {
                'summary': summary,
                'start': {
                    'dateTime': start_dt.isoformat(),
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                },
            }

            if location:
                event_body['location'] = location
            if description:
                event_body['description'] = description

            created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
            logger.info(f"Created calendar event: {created_event.get('id')}")

            start_formatted = start_dt.strftime('%A, %b %d at %I:%M %p')
            return {
                "success": True,
                "result": f"Created event '{summary}' on {start_formatted}.",
                "event_id": created_event.get('id', ''),
                "event_link": created_event.get('htmlLink', ''),
            }

        except PermissionError as e:
            logger.warning(f"Google Calendar auth error: {e}")
            return {"success": False, "error": f"Authentication required: {e}. Call 'refresh_google_auth'."}
        except HttpError as e:
            logger.error(f"Calendar API error: {e}", exc_info=True)
            return {"success": False, "error": f"Google Calendar API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to add calendar event: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@tool_display("Updating a calendar event")
class UpdateCalendarEventTool(BaseTool):
    """Tool to update existing Google Calendar events."""

    name = "update_calendar_event"
    description = (
        "Update an existing event in Google Calendar. Modify title, start time, "
        "duration, location, or description."
    )
    category = "calendar"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "ID of the event to update"
                    },
                    "summary": {
                        "type": "string",
                        "description": "New title for the event"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "New start time in ISO format or natural language"
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "New duration in minutes"
                    },
                    "location": {
                        "type": "string",
                        "description": "New location"
                    },
                    "description": {
                        "type": "string",
                        "description": "New description"
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary')",
                        "default": "primary"
                    }
                },
                "required": ["event_id"]
            }
        }

    async def execute(
        self,
        event_id: str,
        summary: Optional[str] = None,
        start_time: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
        calendar_id: str = "primary",
        **kwargs
    ) -> Dict[str, Any]:
        try:
            service = self._manager.get_calendar_service()
            if not service:
                return {"success": False, "error": "Failed to connect to Google Calendar."}

            existing = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            patch_body: Dict[str, Any] = {}

            if summary is not None:
                patch_body['summary'] = summary
            if location is not None:
                patch_body['location'] = location
            if description is not None:
                patch_body['description'] = description

            if start_time is not None:
                dur = duration_minutes or 60
                new_start, new_end = parse_datetime_flexible(start_time, default_duration_minutes=dur)
                patch_body['start'] = {'dateTime': new_start.isoformat()}
                patch_body['end'] = {'dateTime': new_end.isoformat()}
            elif duration_minutes is not None and 'start' in existing:
                start_raw = existing['start'].get('dateTime')
                if start_raw:
                    start_dt, _ = parse_datetime_flexible(start_raw)
                    patch_body['end'] = {'dateTime': (start_dt + timedelta(minutes=duration_minutes)).isoformat()}

            if not patch_body:
                return {"success": True, "result": "No changes specified for event."}

            updated = service.events().patch(calendarId=calendar_id, eventId=event_id, body=patch_body).execute()
            logger.info(f"Updated calendar event {event_id}")

            return {
                "success": True,
                "result": f"Updated event '{updated.get('summary', event_id)}'.",
                "event_id": event_id,
                "event_link": updated.get('htmlLink', ''),
            }

        except PermissionError as e:
            return {"success": False, "error": f"Authentication required: {e}."}
        except HttpError as e:
            logger.error(f"Calendar API error: {e}", exc_info=True)
            return {"success": False, "error": f"Google Calendar API error: {e}"}
        except Exception as e:
            logger.error(f"Failed to update calendar event: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@tool_display("Deleting a calendar event")
class DeleteCalendarEventTool(BaseTool):
    """Tool to delete calendar events (requires user confirmation)."""

    name = "delete_calendar_event"
    description = (
        "Delete an event from Google Calendar. For safety, requires confirmed=true. "
        "If confirmed is false, returns event details for confirmation."
    )
    category = "calendar"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._manager = GoogleWorkspaceManager()

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "ID of the event to delete"
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set to true to confirm deletion",
                        "default": False
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary')",
                        "default": "primary"
                    }
                },
                "required": ["event_id"]
            }
        }

    async def execute(
        self,
        event_id: str,
        confirmed: bool = False,
        calendar_id: str = "primary",
        **kwargs
    ) -> Dict[str, Any]:
        try:
            service = self._manager.get_calendar_service()
            if not service:
                return {"success": False, "error": "Failed to connect to Google Calendar."}

            try:
                event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
                event_summary = event.get('summary', 'Untitled event')
            except HttpError:
                return {"success": False, "error": f"Event not found: {event_id}"}

            if not confirmed:
                return {
                    "success": False,
                    "needs_confirmation": True,
                    "action": "delete_calendar_event",
                    "details": {"event_id": event_id, "event_summary": event_summary},
                    "message": f"Delete calendar event '{event_summary}'? Please confirm to proceed."
                }

            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            logger.info(f"Deleted calendar event: {event_id}")

            return {
                "success": True,
                "result": f"Deleted event '{event_summary}'."
            }

        except PermissionError as e:
            return {"success": False, "error": f"Authentication required: {e}."}
        except HttpError as e:
            return {"success": False, "error": f"Google Calendar API error: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


def get_upcoming_schedule_context(lookahead_hours: int = 4, max_events: int = 5) -> str:
    """
    Fetch upcoming events and return a concise proactive schedule awareness block.
    Cached in-memory with a short TTL to prevent repeated API calls.
    Returns empty string if no events or on error.
    """
    global _SCHEDULE_CACHE

    now_ts = time.time()
    if (
        now_ts - _SCHEDULE_CACHE["timestamp"] < _CACHE_TTL_SECONDS
        and _SCHEDULE_CACHE["lookahead_hours"] == lookahead_hours
    ):
        return _SCHEDULE_CACHE["context"]

    try:
        manager = GoogleWorkspaceManager()
        service = manager.get_calendar_service()
        if not service:
            return ""

        now_utc = datetime.now(timezone.utc)
        local_tz = _get_local_timezone()
        now_local = datetime.now(local_tz)
        max_time_utc = now_utc + timedelta(hours=lookahead_hours)

        res = service.events().list(
            calendarId='primary',
            timeMin=now_utc.isoformat(),
            timeMax=max_time_utc.isoformat(),
            singleEvents=True,
            orderBy='startTime',
            maxResults=max_events,
        ).execute()

        items = res.get('items', [])
        if not items:
            _SCHEDULE_CACHE["timestamp"] = now_ts
            _SCHEDULE_CACHE["context"] = ""
            _SCHEDULE_CACHE["lookahead_hours"] = lookahead_hours
            return ""

        lines = [
            f"### UPCOMING SCHEDULE (Google Calendar - Next {lookahead_hours} Hours)",
            f"Current local time: {now_local.strftime('%A, %b %d at %I:%M %p')}",
        ]

        immediate_events = []

        for item in items:
            summary = item.get('summary', 'Untitled event')
            start_raw = item['start'].get('dateTime', item['start'].get('date'))
            end_raw = item.get('end', {}).get('dateTime', item.get('end', {}).get('date'))
            location = item.get('location', '')
            loc_str = f" [Location: {location}]" if location else ""

            if start_raw and 'T' in start_raw:
                try:
                    start_dt = datetime.fromisoformat(start_raw.replace('Z', '+00:00')).astimezone(local_tz)
                    rel = _format_relative_time(start_dt, now_local)
                    start_str = start_dt.strftime('%I:%M %p')
                    end_str = ""
                    if end_raw and 'T' in end_raw:
                        end_dt = datetime.fromisoformat(end_raw.replace('Z', '+00:00')).astimezone(local_tz)
                        end_str = f" - {end_dt.strftime('%I:%M %p')}"

                    minutes_until = int((start_dt - now_local).total_seconds() // 60)
                    lines.append(f"- **{summary}**: {rel} ({start_str}{end_str}){loc_str}")

                    if 0 <= minutes_until <= 35:
                        immediate_events.append((summary, rel, start_str, location))
                except Exception:
                    lines.append(f"- **{summary}**: {start_raw}{loc_str}")
            else:
                lines.append(f"- **{summary}**: All day{loc_str}")

        lines.append(
            "\nPROACTIVE AWARENESS DIRECTIVE: You know the user's schedule above. If a class or event is "
            "starting soon (e.g. within 15–30 minutes) or is relevant to what the user is doing, naturally "
            "and casually mention it (e.g., 'By the way, better get going to your class in 15 mins!'). "
            "Speak naturally like a smart friend; don't nag repeatedly if already acknowledged."
        )

        formatted = "\n".join(lines)
        _SCHEDULE_CACHE["timestamp"] = now_ts
        _SCHEDULE_CACHE["context"] = formatted
        _SCHEDULE_CACHE["lookahead_hours"] = lookahead_hours
        return formatted

    except Exception as e:
        logger.debug(f"Schedule context check skipped: {e}")
        return ""
