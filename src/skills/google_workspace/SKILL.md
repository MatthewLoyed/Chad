---
name: google_workspace
description: "Google Workspace: drive and gmail query/search workflows. Native tools only."
version: 1.2.0
tags: [workspace, drive, gmail]
---

## Drive workflow

1. **Named folder or file** → `google_drive_search`
   - Folder: `file_type: "folder"`
   - Doc: `file_type: "document"` (or omit for any type)
2. **Browse a known folder** → `google_drive_list_folder` with `folder_id` from step 1
   - Do not list `root` to hunt for names the user already gave
3. **Read content** → `google_drive_read_file` with `file_id` from search or list

## Gmail workflow

Gmail search and reading are separate operations. Do not assume you can read an email's body directly from inbox listings, which only return short snippets. Follow this exact workflow:

1. **Find target email(s)** → `search_gmail`
   - Use keywords or filters to retrieve the list of matching emails.
   - Example query: `"subject:canvas"` or `"from:sam"`.
2. **Read email content (full thread)** → `read_gmail` with the `message_id` from step 1.
   - Always run `read_gmail` to retrieve the content. This tool automatically resolves the thread ID and retrieves the entire chronological conversation history for you.

### Example Sequence
* **User prompt**: "Check my email about the Canvas reviews and tell me what changes I need to make."
* **Step 1 (Search)**: Invoke `search_gmail(query="Canvas reviews")` to find matching messages.
* **Step 2 (Read)**: Identify the message ID `18f76e282a17ba3c` from the results and invoke `read_gmail(message_id="18f76e282a17ba3c")` to read the full body text (and any thread replies) containing the requested changes.

## Calendar workflow

1. **View upcoming schedule or search events** → `get_calendar_events`
   - Specific keywords/topics: `search_query: "Project"`, `search_query: "Doctor"`
   - Today / upcoming window: `hours_ahead: 24` or `time_min` / `time_max`
2. **Add an event** → `add_calendar_event`
   - Specify `summary` (title), `start_time` (e.g. ISO format or natural time like 'tomorrow 2pm'), `duration_minutes` or `end_time`, `location`.
3. **Update an event** → `update_calendar_event` with `event_id` and changed parameters.
4. **Delete an event** → `delete_calendar_event` with `event_id` (requires user confirmation).

## Rules

- Search before browse/read when the user names a target.
- Return at most 5 items unless the user asks for more.
- Use native `google_drive_*`, `*gmail*`, and `*calendar*` tools only — no MCP, no browser.
