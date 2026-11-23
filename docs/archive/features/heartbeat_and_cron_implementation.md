# Server-Side Heartbeat & Cron Scheduler Implementation

## Overview

This implementation adds autonomous monitoring and scheduling capabilities to Chad's Brain (server), following the OpenClaw architecture pattern.

**Key Features:**
- **Heartbeat Service**: Autonomous periodic awareness (checks every 30 minutes)
- **Cron Scheduler**: Precise task scheduling with cron expressions
- **24/7 Server-Side Execution**: Runs independently of client connections
- **SYSTEM-Initiated Turns**: Uses ConversationOrchestrator with SYSTEM initiator
- **Persistent Task Storage**: SQLite-based TaskRepository for reliability

## Architecture

```
┌─────────────────────────────────┐
│   ChadServer (Brain on Render)  │  ◄─── Runs 24/7
├─────────────────────────────────┤
│  HeartbeatService               │  ◄─── Wakes every 30 min
│  - Reads the heartbeat persona   │
│  - Triggers SYSTEM turn         │
│  - Logs HEARTBEAT_OK or alerts  │
├─────────────────────────────────┤
│  CronScheduler                  │  ◄─── Checks every 60 sec
│  - Queries TaskRepository       │
│  - Executes due tasks           │
│  - Updates next_run times       │
└──────┬────────────┬─────────────┘
       │            │
       ▼            ▼
┌──────────┐  ┌──────────────────┐
│HEARTBEAT │  │ TaskRepository   │
│.md       │  │ (SQLite)         │
└──────────┘  └──────────────────┘
       │            │
       └────────────┘
              │
              ▼
┌─────────────────────────────────┐
│ ConversationOrchestrator        │
│ - start_turn(initiator=SYSTEM)  │  ◄─── SYSTEM-initiated turns
└─────────────────────────────────┘
```

## Implementation Details

### Part 1: Heartbeat Service

**Files Created:**
- `src/core/monitoring/heartbeat_service.py` - Main heartbeat loop
- `src/tools/monitoring/monitoring_tools.py` - Gmail, Twitter, Calendar check tools (stubs)
- `HEARTBEAT.md` - Checklist template

**Files Modified:**
- `src/remote/server.py` - Integrated HeartbeatService into Brain startup/shutdown

**How It Works:**
1. HeartbeatService starts when Brain initializes
2. Every 30 minutes, reads `HEARTBEAT.md` checklist
3. Triggers a SYSTEM turn via ConversationOrchestrator
4. AI processes checklist in single batched turn (cost-efficient)
5. Returns "HEARTBEAT_OK" or triggers notifications

**Usage Example:**
```python
# Heartbeat starts automatically with Brain server
# Check HEARTBEAT.md to customize monitoring tasks

# Manual execution (for testing):
await heartbeat_service._execute_heartbeat()
```

### Part 2: Cron Scheduler

**Files Created:**
- `src/core/scheduling/cron_scheduler.py` - Main scheduler service

**Files Modified:**
- `src/remote/server.py` - Integrated CronScheduler and TaskRepository
- `requirements.txt` - Added croniter>=2.0.0 dependency

**How It Works:**
1. CronScheduler starts when Brain initializes
2. Every 60 seconds, queries TaskRepository for due tasks
3. For each due task:
   - Marks task as RUNNING
   - Triggers SYSTEM turn with task description
   - Updates task status (COMPLETED/FAILED)
   - Calculates next run time using croniter
   - Resets to SCHEDULED for recurring tasks

**Usage Example:**
```python
# Add a daily task at 9am
task_id = server.add_cron_task(
    name="morning_summary",
    description="Summarize yesterday's activity and plan today",
    schedule="0 9 * * *"  # 9am daily
)

# List all tasks
tasks = server.list_cron_tasks()

# Remove a task
server.remove_cron_task(task_id)
```

**Cron Expression Examples:**
```
"0 9 * * *"        # 9am daily
"0 */2 * * *"      # Every 2 hours
"30 8 * * 1"       # 8:30am every Monday
"0 0 1 * *"        # Midnight on 1st of month
"*/15 * * * *"     # Every 15 minutes
```

## Database Schema

**agent_tasks table:**
```sql
CREATE TABLE agent_tasks (
    id INTEGER PRIMARY KEY,
    task_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    trigger TEXT NOT NULL,        -- 'cron', 'heartbeat', 'user', etc.
    status TEXT NOT NULL,          -- 'scheduled', 'running', 'completed', 'failed'
    schedule TEXT,                 -- Cron expression
    created_at DATETIME,
    last_run DATETIME,
    next_run DATETIME,
    metadata TEXT                  -- JSON metadata
)
```

**task_executions table:**
```sql
CREATE TABLE task_executions (
    id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args TEXT,                     -- JSON args
    result TEXT,                   -- JSON result
    success INTEGER NOT NULL,
    duration REAL,
    timestamp DATETIME,
    trigger_source TEXT,
    error TEXT,
    FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id)
)
```

## Configuration

**Heartbeat Interval:**
```python
# In server initialization (default: 30 minutes)
await self._heartbeat_service.start(interval_minutes=30)

# Can be changed to any value (e.g., 15 minutes):
await self._heartbeat_service.start(interval_minutes=15)
```

**Cron Poll Interval:**
```python
# Fixed at 60 seconds in CronScheduler._scheduler_loop()
POLL_INTERVAL = 60.0  # Check every 60 seconds
```

**Task Database Location:**
```python
# In server initialization
db_path = Path("data/agent_tasks.db")
self._task_repository = TaskRepository(str(db_path))
```

## Monitoring Tools (Stubs)

The following monitoring tools are available but not yet fully implemented:

1. **gmail_check_inbox** - Check Gmail for important emails
   - Status: Stub (returns empty results)
   - TODO: Add Gmail API integration with OAuth

2. **twitter_check_user** - Check if user posted recently
   - Status: Stub (returns empty results)
   - TODO: Add Twitter API integration

3. **youtube_check_channel** - Check for new uploads
   - Status: Stub (returns empty results)
   - TODO: Add YouTube Data API integration

4. **google_calendar_check** - Check for upcoming events
   - Status: Stub (returns empty results)
   - TODO: Add Google Calendar API integration

## Testing

### Test Heartbeat Service

```bash
# Start Brain server
$env:PYTHONPATH="."; python src/remote/server.py

# Check logs for heartbeat activity
grep "Heartbeat" logs/*.log
```

**Expected output:**
```
[ HEARTBEAT ] : Autonomous heartbeat service started (30 min interval)
Executing heartbeat check...
Heartbeat turn started: turn_1_1234567890
```

### Test Cron Scheduler

```python
# Connect to running Brain and add a test task
import asyncio
from src.remote.server import ChadServer

# Add a task that runs every 2 minutes (for testing)
task_id = server.add_cron_task(
    name="test_task",
    description="This is a test task. Return 'TEST_COMPLETE' when done.",
    schedule="*/2 * * * *"  # Every 2 minutes
)

print(f"Added task: {task_id}")

# List tasks
tasks = server.list_cron_tasks()
print(f"Scheduled tasks: {len(tasks)}")

# Wait and check execution
await asyncio.sleep(130)  # Wait 2+ minutes
grep "Executing cron task: test_task" logs/*.log
```

**Expected output:**
```
[ CRON ] : Cron scheduler service started
Added cron task 'test_task' with schedule '*/2 * * * *'. Next run: 2026-03-16 02:30:00
Found 1 due tasks to execute
Executing cron task: test_task (ID: abc-123-def)
Task test_task completed successfully. Next run: 2026-03-16 02:32:00
```

### Test SYSTEM Initiator

```python
# Verify SYSTEM turns are properly handled
from src.domain.models import TurnInitiator

turn = await orchestrator.start_turn(
    user_input="Test SYSTEM turn",
    category="system",
    initiator=TurnInitiator.SYSTEM,
    intent="test"
)

print(f"Turn ID: {turn.turn_id}")
print(f"Initiator: {turn.initiator.value}")
```

## Integration with Telegram (Deferred)

The HeartbeatService is designed to send Telegram notifications but this integration is deferred to a separate implementation phase.

**Current behavior:**
- Important findings logged with `📢 NOTIFY USER:` prefix
- Can be monitored in server logs

**Future implementation:**
```python
# Will add Telegram bot integration
async def send_telegram_notification(message: str):
    # Send via Telegram Bot API
    pass

# In heartbeat response handler:
if "IMPORTANT" in response:
    await send_telegram_notification(response)
```

## Deployment Notes

**On Render.com:**
1. Brain runs 24/7 (always-on dyno)
2. Heartbeat and cron run server-side
3. No dependency on client connections
4. Logs visible in Render dashboard

**Environment Variables:**
```bash
# No new env vars required for basic functionality
# Future: Add for API integrations
GMAIL_OAUTH_CREDENTIALS=...
TWITTER_API_KEY=...
YOUTUBE_API_KEY=...
TELEGRAM_BOT_TOKEN=...
```

## Future Enhancements

1. **Telegram Integration** - Send notifications to user's phone
2. **API Integrations** - Gmail, Twitter, Calendar, YouTube
3. **Web Dashboard** - UI for managing cron tasks
4. **Task Templates** - Pre-built task templates (daily summary, etc.)
5. **Retry Logic** - Automatic retry for failed tasks
6. **Task Dependencies** - Chain tasks together
7. **Conditional Execution** - Run tasks based on conditions
8. **Notification Channels** - Email, SMS, Discord, etc.

## Troubleshooting

**Heartbeat not executing:**
- Check `HEARTBEAT.md` exists in project root
- Verify ConversationOrchestrator is initialized
- Check logs for `HeartbeatService` errors

**Cron tasks not executing:**
- Verify task's `next_run` time has passed
- Check task status (should be `SCHEDULED`)
- Verify cron expression is valid
- Check `data/agent_tasks.db` exists

**SYSTEM turns failing:**
- Verify `TurnInitiator.SYSTEM` is imported correctly
- Check ConversationOrchestrator supports SYSTEM initiator
- Review turn logs for execution errors

## Success Criteria

✅ **Heartbeat Service:**
- [x] Starts automatically with Brain server
- [x] Executes check every 30 minutes
- [x] Reads HEARTBEAT.md checklist
- [x] Triggers SYSTEM-initiated turns
- [x] Runs independently of client connections

✅ **Cron Scheduler:**
- [x] Starts automatically with Brain server
- [x] Queries TaskRepository every 60 seconds
- [x] Executes due tasks via SYSTEM turns
- [x] Updates task status and next_run times
- [x] Provides API for task management

✅ **Infrastructure:**
- [x] TaskRepository with SQLite persistence
- [x] AgentTask domain model with status tracking
- [x] ConversationOrchestrator SYSTEM initiator support
- [x] Monitoring tools registered in UnifiedRegistry

## References

- **Plan Document**: Implementation plan at top of this conversation
- **OpenClaw Pattern**: Heartbeat loop architecture reference
- **Cron Expressions**: https://crontab.guru/
- **Croniter Library**: https://github.com/kiorky/croniter
