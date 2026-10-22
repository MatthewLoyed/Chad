import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta

from src.tools.workspace.calendar import (
    GetCalendarEventsTool,
    AddCalendarEventTool,
    UpdateCalendarEventTool,
    DeleteCalendarEventTool,
    get_upcoming_schedule_context,
    _format_relative_time,
    parse_datetime_flexible,
)
from src.tools.monitoring.monitoring_tools import GoogleCalendarCheckTool
from src.core.intelligence.calendar_schedule_client import CalendarScheduleClient


def test_format_relative_time():
    now = datetime(2026, 10, 13, 12, 0, 0)
    
    # In 15 minutes
    in_15 = datetime(2026, 10, 13, 12, 15, 0)
    assert _format_relative_time(in_15, now) == "starts in 15 mins"
    
    # In 2 hours
    in_2h = datetime(2026, 10, 13, 14, 0, 0)
    assert _format_relative_time(in_2h, now) == "starts in 2 hrs"

    # Started 10 minutes ago
    ago_10 = datetime(2026, 10, 13, 11, 50, 0)
    assert _format_relative_time(ago_10, now) == "started 10m ago"


def test_parse_datetime_flexible():
    start_dt, end_dt = parse_datetime_flexible("2026-10-13T14:30:00", default_duration_minutes=45)
    assert start_dt.hour == 14
    assert start_dt.minute == 30
    assert end_dt.hour == 15
    assert end_dt.minute == 15


@pytest.mark.asyncio
async def test_get_calendar_events_tool():
    tool = GetCalendarEventsTool()
    
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_list = MagicMock()
    
    mock_list.execute.return_value = {
        "items": [
            {
                "id": "event1",
                "summary": "Software Engineering Class",
                "start": {"dateTime": "2026-10-13T12:30:00Z"},
                "end": {"dateTime": "2026-10-13T13:45:00Z"},
                "location": "Brock Hall 204",
                "htmlLink": "https://calendar.google.com/event1"
            }
        ]
    }
    mock_events.list.return_value = mock_list
    mock_service.events.return_value = mock_events
    
    with patch.object(tool._manager, "get_calendar_service", return_value=mock_service):
        result = await tool.execute(limit=5, hours_ahead=2)
        
        assert result["success"] is True
        assert result["count"] == 1
        event = result["events"][0]
        assert event["summary"] == "Software Engineering Class"
        assert event["location"] == "Brock Hall 204"
        assert "starts_in_minutes" in event


@pytest.mark.asyncio
async def test_add_calendar_event_tool():
    tool = AddCalendarEventTool()
    
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_insert = MagicMock()
    
    mock_insert.execute.return_value = {
        "id": "new_event_123",
        "htmlLink": "https://calendar.google.com/new_event_123"
    }
    mock_events.insert.return_value = mock_insert
    mock_service.events.return_value = mock_events
    
    with patch.object(tool._manager, "get_calendar_service", return_value=mock_service):
        result = await tool.execute(
            summary="Dentist Appointment",
            start_time="2026-10-14T09:00:00",
            duration_minutes=30,
            location="Dental Clinic"
        )
        
        assert result["success"] is True
        assert result["event_id"] == "new_event_123"
        assert "Dentist Appointment" in result["result"]


@pytest.mark.asyncio
async def test_update_calendar_event_tool():
    tool = UpdateCalendarEventTool()
    
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_get = MagicMock()
    mock_patch = MagicMock()
    
    mock_get.execute.return_value = {
        "id": "evt_456",
        "summary": "Old Title",
        "start": {"dateTime": "2026-10-14T10:00:00Z"},
    }
    mock_patch.execute.return_value = {
        "id": "evt_456",
        "summary": "Updated Title",
        "htmlLink": "https://calendar.google.com/evt_456"
    }
    mock_events.get.return_value = mock_get
    mock_events.patch.return_value = mock_patch
    mock_service.events.return_value = mock_events
    
    with patch.object(tool._manager, "get_calendar_service", return_value=mock_service):
        result = await tool.execute(
            event_id="evt_456",
            summary="Updated Title",
            location="Room 101"
        )
        
        assert result["success"] is True
        assert result["event_id"] == "evt_456"
        assert "Updated Title" in result["result"]


@pytest.mark.asyncio
async def test_delete_calendar_event_confirmation():
    tool = DeleteCalendarEventTool()
    
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_get = MagicMock()
    mock_delete = MagicMock()
    
    mock_get.execute.return_value = {
        "id": "evt_del",
        "summary": "Team Meeting",
    }
    mock_delete.execute.return_value = {}
    mock_events.get.return_value = mock_get
    mock_events.delete.return_value = mock_delete
    mock_service.events.return_value = mock_events
    
    with patch.object(tool._manager, "get_calendar_service", return_value=mock_service):
        # 1. Without confirmation
        unconfirmed = await tool.execute(event_id="evt_del", confirmed=False)
        assert unconfirmed["success"] is False
        assert unconfirmed["needs_confirmation"] is True
        assert "Team Meeting" in unconfirmed["message"]
        mock_delete.execute.assert_not_called()
        
        # 2. With confirmation
        confirmed = await tool.execute(event_id="evt_del", confirmed=True)
        assert confirmed["success"] is True
        mock_delete.execute.assert_called_once()


@pytest.mark.asyncio
async def test_google_calendar_check_monitoring_tool():
    tool = GoogleCalendarCheckTool()
    
    with patch("src.tools.workspace.calendar.GetCalendarEventsTool") as mock_cal_tool_cls:
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock(return_value={
            "success": True,
            "events": [
                {
                    "summary": "Algorithms Lecture",
                    "start_formatted": "Tuesday, Oct 13 at 01:00 PM",
                    "relative_time": "starts in 45 mins",
                    "starts_in_minutes": 45,
                    "location": "Science Hall 101",
                    "link": "https://calendar.google.com/alg"
                }
            ]
        })
        mock_cal_tool_cls.return_value = mock_instance
        
        res = await tool.execute(hours_ahead=2)
        assert len(res["upcoming_events"]) == 1
        ev = res["upcoming_events"][0]
        assert ev["title"] == "Algorithms Lecture"
        assert ev["relative_time"] == "starts in 45 mins"
        assert ev["starts_in_minutes"] == 45


@pytest.mark.asyncio
async def test_calendar_schedule_client():
    client = CalendarScheduleClient(lookahead_hours=3)
    
    with patch("src.tools.workspace.calendar.get_upcoming_schedule_context", return_value="### UPCOMING SCHEDULE\n- Class in 15m"):
        context = await client.search()
        assert context is not None
        assert "UPCOMING SCHEDULE" in context
        assert "Class in 15m" in context
