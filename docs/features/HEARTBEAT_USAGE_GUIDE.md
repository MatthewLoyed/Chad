# Heartbeat Usage Guide - Talk to Chad to Configure Monitoring

## What is Heartbeat?

**Heartbeat = Periodic awareness system** that runs every **30 minutes** in the main session.

Think of it like Chad checking his todo list every half hour to see if anything needs attention.

---

## How It Works

### The Flow:

1. **Every 30 minutes**, heartbeat wakes up
2. **Reads checklist** from `docs/HEARTBEAT.md` (dynamically generated)
3. **Checks all items** in ONE turn (batched for efficiency)
4. **If nothing important:** Returns "HEARTBEAT_OK" (silent)
5. **If something important:** Notifies you immediately

---

## Configure Through Conversation

### Example 1: Monitor Email

```
You: "Chad, let me know when I get a new email that's important"

Chad: "I'll check your Gmail every 30 minutes for emails containing
       'urgent', 'important', or 'asap'. You'll be notified immediately
       when I find something."

[30 minutes later]
Chad: "URGENT EMAIL - Boss needs Q4 report by EOD - Check Gmail now"
```

**What happened:**
- Chad called `manage_heartbeat` tool
- Added email check to configuration
- Regenerated `docs/HEARTBEAT.md` with new check
- Next heartbeat (30 min) will check Gmail

---

### Example 2: Monitor Calendar

```
You: "Also remind me about upcoming meetings"

Chad: "I'll check your calendar every 30 minutes for events in the next 2 hours."

[30 minutes later, if meeting found]
Chad: "UPCOMING MEETING - Team standup in 45 minutes at Conference Room A"
```

---

### Example 3: List What's Being Monitored

```
You: "What are you monitoring in heartbeat?"

Chad: "Heartbeat monitoring (2 checks):
       1. Check Gmail for urgent, important, asap emails
       2. Check calendar for events in next 2 hours"
```

---

### Example 4: Remove a Check

```
You: "Stop checking my email in heartbeat"

Chad: "Removed heartbeat check: Check Gmail for urgent, important, asap emails"
```

---

## What Chad Can Monitor

### ✅ Currently Implemented:

| Check Type | Tool Used | What It Does |
|------------|-----------|--------------|
| **Email** | `gmail_check_inbox` | Checks Gmail for unread emails matching keywords |
| **Calendar** | `google_calendar_check` | Checks Google Calendar for upcoming events |

### 📋 Placeholders (Not Yet Implemented):

| Check Type | Tool | Status |
|------------|------|--------|
| Twitter | `twitter_check_user` | ⚠️ Returns placeholder |
| YouTube | `youtube_check_channel` | ⚠️ Returns placeholder |
| System Health | `system_health_check` | ⚠️ Not created yet |

---

## How Configuration Works

### 1. User Says Something

```
"Let me know when I get important emails"
```

### 2. Chad Calls Tool

```python
manage_heartbeat(
    action="add",
    check_type="email",
    parameters={
        "keywords": ["urgent", "important", "asap"],
        "max_results": 5
    }
)
```

### 3. Configuration Saved

**File:** `config/heartbeat_config.json`

```json
{
  "checks": [
    {
      "id": "a8d2238b",
      "type": "email",
      "parameters": {
        "keywords": ["urgent", "important", "asap"],
        "max_results": 5
      },
      "tool": "gmail_check_inbox",
      "description": "Check Gmail for urgent, important, asap emails",
      "enabled": true,
      "added_at": "2026-03-16T17:18:51"
    }
  ],
  "preferences": {},
  "last_updated": "2026-03-16T17:18:51"
}
```

### 4. HEARTBEAT.md Regenerated

**File:** `docs/HEARTBEAT.md` (Auto-generated, don't edit manually!)

```markdown
# Heartbeat Checklist

## Check Gmail for urgent, important, asap emails
- Tool: `gmail_check_inbox`
- Parameters: {"keywords": ["urgent", "important", "asap"], "max_results": 5}

## Instructions for AI
1. Check ALL items above in ONE turn (batched for efficiency)
2. If nothing important found: Return exactly "HEARTBEAT_OK"
3. If important events found: Send ONE notification with summary
```

### 5. Heartbeat Executes

Every 30 minutes:
1. Load `docs/HEARTBEAT.md`
2. Call `gmail_check_inbox` with specified parameters
3. Analyze results
4. If important emails found → Notify user
5. If nothing found → Return "HEARTBEAT_OK" (silent)

---

## Heartbeat vs Cron - When to Use Which?

### Use **Heartbeat** When:

- ✅ **Multiple checks** (batch them together)
- ✅ **Context-aware** decisions needed
- ✅ **Conversational** continuity matters
- ✅ **Low overhead** monitoring
- ✅ **Flexible timing** okay (within a few minutes)

**Examples:**
- "Let me know when I get important emails"
- "Check my calendar for upcoming meetings"
- "Monitor system health"

### Use **Cron** When:

- ✅ **Exact timing** required (9am sharp)
- ✅ **Standalone** tasks
- ✅ **One-shot** reminders
- ✅ **Isolated** from main session

**Examples:**
- "Remind me in 20 minutes to call mom" (cron with AT trigger)
- "Send daily report at 9am" (cron with CRON trigger)
- "Check project status every 2 hours" (cron with EVERY trigger)

---

## Configuration Reference

### Supported Check Types

#### 1. Email Monitoring

```python
manage_heartbeat(
    action="add",
    check_type="email",
    parameters={
        "keywords": ["urgent", "important", "boss"],  # Search for these
        "max_results": 5  # Return top 5 matches
    }
)
```

#### 2. Calendar Monitoring

```python
manage_heartbeat(
    action="add",
    check_type="calendar",
    parameters={
        "hours_ahead": 2  # Look ahead 2 hours
    }
)
```

#### 3. Custom Check

```python
manage_heartbeat(
    action="add",
    check_type="custom",
    parameters={
        "description": "Check for new tweets from @elonmusk",
        "tool": "twitter_check_user",
        "params": {"username": "elonmusk"}
    }
)
```

### Actions

| Action | Description | Required Params |
|--------|-------------|-----------------|
| `add` | Add new check | `check_type`, `parameters` |
| `remove` | Remove check | `check_id` |
| `list` | List all checks | None |
| `update_preferences` | Update notification preferences | `preferences` |

---

## Testing

### Test the Configuration System

```bash
# Run the demo (simulates conversation)
python tests/demo_heartbeat_config.py --conversation
```

**Output:**
```
USER: 'Chad, let me know when I get a new email that's important'
CHAD: 'Added heartbeat check: Check Gmail for urgent, important, asap emails'

USER: 'What are you monitoring in heartbeat?'
CHAD: 'Heartbeat monitoring (1 checks):
       1. Check Gmail for urgent, important, asap emails'
```

### Test End-to-End

1. **Configure a check:**
   ```
   Say to Chad: "Let me know when I get important emails"
   ```

2. **Verify configuration:**
   ```bash
   cat config/heartbeat_config.json
   cat docs/HEARTBEAT.md
   ```

3. **Wait for heartbeat** (30 minutes)

4. **Check logs:**
   ```bash
   tail -f logs/chad.log | grep -i heartbeat
   ```

**Expected:**
```
INFO | HeartbeatService | Executing heartbeat check...
INFO | HeartbeatService | Heartbeat turn started: turn_abc123
```

---

## Current Limitations

### ⚠️ Placeholder Tools

Some monitoring tools are not fully implemented yet:

- **Twitter monitoring:** Returns "not yet implemented"
- **YouTube monitoring:** Returns "not yet implemented"
- **System health:** Tool doesn't exist yet

To implement these, update:
- `src/tools/monitoring/monitoring_tools.py`
- Add real API integration (like we did for Gmail/Calendar)

### 💡 Future Enhancements

- **UI Dashboard:** Visual display of heartbeat checks
- **Notification channels:** Email, SMS, Telegram
- **Conditional checks:** "Only check email during work hours"
- **Priority levels:** "Only notify for high-priority events"
- **Historical tracking:** "Show me what heartbeat found today"

---

## Files Changed

### New Files:

- `src/tools/system/heartbeat_manager.py` - Tool to configure heartbeat
- `config/heartbeat_config.json` - Dynamic configuration (auto-created)
- `tests/demo_heartbeat_config.py` - Demo script
- `docs/HEARTBEAT_USAGE_GUIDE.md` - This file

### Modified Files:

- `src/tools/monitoring/monitoring_tools.py` - Real Gmail/Calendar integration
- `docs/HEARTBEAT.md` - Now auto-generated, don't edit manually

---

## Quick Reference

### Add Email Monitoring
```
"Chad, let me know when I get important emails"
```

### Add Calendar Monitoring
```
"Remind me about upcoming meetings"
```

### List Current Checks
```
"What are you monitoring?"
```

### Remove a Check
```
"Stop checking my email"
```

### Verify Heartbeat is Running
```bash
tail -f logs/chad.log | grep "Executing heartbeat"
```

---

## Summary

✅ **Heartbeat is configurable through conversation**
✅ **Real Gmail and Calendar monitoring implemented**
✅ **Dynamic HEARTBEAT.md generation**
✅ **Persistent configuration storage**
⚠️ **Some tools still need API integration**
💡 **UI dashboard coming later**

**You can now say:** "Chad, let me know when I get a new email that's important" and it will work! 🎉
