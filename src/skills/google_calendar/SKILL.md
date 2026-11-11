---
name: google_calendar
description: "Google Calendar viewing and scheduling workflows. Native tools only."
version: 1.0.0
tags: [calendar, schedule, events]
---

## Calendar workflow

1. **View upcoming schedule or search events** → `get_calendar_events`
   - Specific keywords/topics: `search_query: "Project"`, `search_query: "Doctor"`
   - Whole local day: `day: "today"` or `day: "YYYY-MM-DD"`, from local midnight to the next local midnight.
   - Rolling window: `hours_ahead: 24`. For custom bounds use timezone-qualified `time_min` and `time_max`. Do not combine these bounds with `day`.
2. **Add an event** → `add_calendar_event`
   - Specify `summary` (title), `start_time` (e.g. ISO format or natural time like 'tomorrow 2pm'), `duration_minutes` or `end_time`, `location`.
3. **Update an event** → `update_calendar_event` with `event_id` and changed parameters.
4. **Delete an event** → `delete_calendar_event` with `event_id` (requires user confirmation).

## Rules
- Use native `get_calendar_events`, `add_calendar_event`, `update_calendar_event`, `delete_calendar_event`.
