---
name: gmail
description: "Gmail search, read, and compose workflows. Native tools only."
version: 1.0.0
tags: [gmail, email]
---

## Gmail workflow

Gmail search and reading are separate operations. Do not assume you can read an email's body directly from inbox listings, which only return short snippets.

1. **Find target email(s)** → `search_gmail`
   - Use keywords or filters to retrieve the list of matching emails.
   - Example query: `"subject:canvas"` or `"from:sam"`.
2. **Read email content (full thread)** → `read_gmail` with the `message_id` from step 1.
   - Resolves thread ID and retrieves chronological conversation history.
3. **Send or Draft email** → `send_email` (direct send) or `create_gmail_draft` (to review draft before sending).

## Rules
- Use native `search_gmail`, `read_gmail`, `send_email`, `create_gmail_draft`, `draft_gmail_reply`.
- Search before read when the user names an email target.
- Return at most 5 items unless the user asks for more.
