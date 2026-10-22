---
scope: event
---

SYSTEM NOTIFICATION: A new Gmail sync event occurred for history id {history_id}.

## Email Details:
{email_details}

## Action Instructions:
- If this email is from a real human and requires the user's attention, summarize it casually in 1–2 short lines.
- If it does not require his attention, call `stay_silent(reason="no urgent action needed")`.
