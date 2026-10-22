# Telegram Bidirectional Messaging Integration

Complete guide for setting up and using Telegram messaging with Chad AI Assistant.

## Overview

Chad's Telegram integration is a **pocket remote**, not a second full app:

- **Send messages from anywhere** via Telegram on your phone
- **Photos in** — contact desk (9B) sees images; escalate only when tools are needed
- **Links and files out** — answers as new messages; PDFs/diagrams sent as documents
- **Autonomous notifications** from cron/heartbeat and Gmail webhooks delivered to mobile regardless of desktop limb status
- **Isolated conversations** — turns initiated on Telegram do NOT leak onto or render inside the desktop GUI window; each channel maintains clean independent turn buckets and session keys

Use the **PWA/desktop** for coding, browser automation, interactive cards, and long sessions.

### Lanes

1. **contact** (`qwen/qwen3.5-9b`) — greetings, photo Q&A, escalate decision  
2. **telegram_agent** — fixed tool menu (search, Gmail, calendar, memory, places, visual exports)  
3. Full **supervisor** — PWA/desktop only (not used for Telegram escalate)

## Architecture

### Components

1. **TelegramClient** (`src/remote/telegram/telegram_client.py`)
   - Lightweight async HTTP client for Telegram Bot API
   - Methods: `send_message()`, `send_document()`, `send_photo()`, `get_file()` / `download_file()`, `get_me()`
   - Uses httpx (already in requirements.txt)

2. **TelegramChatRepository** (`src/infrastructure/repositories/telegram_chat_repository.py`)
   - Persists chat_id ↔ session_key mappings
   - SQLite database at `data/telegram_chats.db`
   - Tracks user metadata (username, first_name, last_name)

3. **TelegramResponseRouter** (`src/remote/telegram/telegram_response_router.py`)
   - Event-driven response routing via EventBus
   - Listens for TurnCompleted events
   - Always `sendMessage` (send-only); delivers turn artifacts via `sendDocument`

4. **SendTelegramMessageTool** (`src/tools/messaging/send_telegram_message.py`)
   - Enables autonomous Telegram notifications
   - Used by cron jobs, heartbeat, and skills
   - Idempotency-safe (prevents duplicate sends)

### Message Flow

**Incoming Messages (User → Chad):**
```
User sends text or photo via Telegram
  ↓
Telegram webhook POST to /webhooks/telegram
  ↓
WebhookManager verifies signature + allowlist
  ↓
Optional getFile download → content_blocks
  ↓
session_key + category=contact (9B)
  ↓
Greeting/photo answer OR escalate → telegram_agent
  ↓
TurnCompleted → sendMessage (+ sendDocument for artifacts)
```

**Outgoing Messages (Chad → User):**
```
HeartbeatService or CronScheduler triggers
  ↓
AI decides to use send_telegram_message tool
  ↓
Tool execution: send_telegram_message(message="You have 2 urgent emails")
  ↓
TelegramClient.send_message() to most recent active chat
  ↓
User receives notification on phone
```

## Setup Instructions

### Step 1: Create Telegram Bot

1. Open Telegram app and search for **@BotFather**
2. Send `/newbot` command
3. Choose bot name: **"Chad AI Assistant"** (or any name)
4. Choose username: **"chad_ai_bot"** (must end in 'bot')
5. **Copy the bot token** (format: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
6. (Optional) Send `/setdescription` to add description
7. (Optional) Send `/setuserpic` to upload profile photo

### Step 2: Configure Bot Token

**Option A: Using config/secrets.yaml (recommended)**

```yaml
# config/secrets.yaml
telegram:
  bot_token: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
  webhook_secret_token: "generate_random_32_char_string"  # Optional
```

**Option B: Using environment variables**

```bash
export TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
export TELEGRAM_WEBHOOK_SECRET="your_secret_here"
```

### Step 3: Deploy Brain to Cloud (if not already done)

**Local Testing:**
```powershell
# Start Brain locally
$env:PYTHONPATH="."; python src/remote/server.py
```

**Production (Render.com):**
```bash
# Commit changes
git add .
git commit -m "feat(telegram): add bidirectional messaging integration"
git push origin main

# Render auto-deploys from main branch
# Add environment variables in Render dashboard:
#   TELEGRAM_BOT_TOKEN = your_token_here
```

### Step 4: Register Webhook with Telegram

**After deploying to cloud:**

```bash
python scripts/setup_telegram_webhook.py \
    --url https://your-app.onrender.com/webhooks/telegram \
    --secret your_webhook_secret_here
```

**For local testing (using ngrok):**

```bash
# Terminal 1: Start ngrok
ngrok http 8767

# Terminal 2: Register webhook
python scripts/setup_telegram_webhook.py \
    --url https://abc123.ngrok.io/webhooks/telegram
```

**Verify webhook:**
```bash
# Check status
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"

# Delete webhook (if needed)
python scripts/setup_telegram_webhook.py --delete
```

### Step 5: Test Integration

1. Open Telegram app on your phone
2. Search for your bot: **@chad_ai_bot** (or your chosen username)
3. Send: **"Hello, are you there?"**
4. Should receive response within 2-3 seconds

## Usage Examples

### User-Initiated Conversations

```
You: Check my email for anything urgent

Chad: I found 2 urgent emails:
1. From: boss@company.com
   Subject: Q1 Review Meeting Tomorrow
   Received: 5 minutes ago

2. From: client@example.com
   Subject: URGENT: Server Down
   Received: 12 minutes ago

Would you like me to read the full contents?
```

### Autonomous Notifications (Heartbeat)

```
[Heartbeat runs every 30 minutes]

Chad: 📧 You have 3 new important emails:
- URGENT: Production deployment approval needed
- Meeting reminder: Team standup in 15 minutes
- Invoice #12345 payment overdue

Need me to take any action?
```

### Scheduled Tasks (Cron)

```yaml
# Create reminder via cron
task:
  trigger: "EVERY 1d AT 09:00"
  prompt: "Send morning briefing with today's calendar and important emails"
```

```
[Every day at 9:00 AM]

Chad: ☀️ Good morning! Here's your daily briefing:

📅 Calendar Today:
- 10:00 AM: Team Standup
- 2:00 PM: Client Presentation
- 4:00 PM: 1-on-1 with Manager

📧 Important Emails:
- 2 unread from boss@company.com
- 1 calendar invite pending response

Have a great day!
```

## Features

### Phase 1 (Implemented) ✅

- ✅ Text message sending/receiving
- ✅ Bidirectional communication
- ✅ Session key isolation (separate memory per chat)
- ✅ Autonomous notifications via `send_telegram_message` tool
- ✅ Webhook-based updates (efficient for cloud)
- ✅ HTML formatting support
- ✅ Chat metadata tracking
- ✅ Idempotency protection

### Phase 2 (Future) 🚧

- 🚧 Streaming responses (message editing for progress updates)
- 🚧 Inline buttons for interactive prompts
- 🚧 Image support (send/receive photos)
- 🚧 Voice message transcription
- 🚧 Document upload and processing
- 🚧 Group chat support with @mention detection

## Session Management

### Session Key Format

**Format:** `main:{user_id}:telegram:{chat_id}`

**Examples:**
- `main:987654321:telegram:123456789` (private chat)
- `main:987654321:telegram:-1001234567890` (group chat)

### Conversation Isolation

Each Telegram chat gets its own memory database:
- Different users = separate conversations
- Same user, different chats = separate conversations
- Memory persisted at `data/memory/main/{user_id}/telegram_{chat_id}.db`

**Example:**
```
User A (chat 123): "Remember my favorite color is blue"
User B (chat 456): "What's my favorite color?"
Chad to B: "I don't know your favorite color yet"

User A (chat 123): "What's my favorite color?"
Chad to A: "Your favorite color is blue"
```

## Tool Usage (For AI)

### SendTelegramMessage Tool

**Schema:**
```json
{
  "name": "send_telegram_message",
  "description": "Send a message to the user via Telegram",
  "parameters": {
    "message": {
      "type": "string",
      "description": "Message text (supports HTML formatting)"
    },
    "chat_id": {
      "type": "integer",
      "description": "Optional: specific chat ID (defaults to most recent)"
    }
  }
}
```

**Usage in Prompts:**
```
"If you find any urgent emails, use send_telegram_message tool to notify me immediately"
"Check my calendar every morning at 9 AM and send me a briefing via Telegram"
"Monitor GitHub issues and alert me via Telegram if any high-priority issues are created"
```

**HTML Formatting:**
```html
<b>bold</b>
<i>italic</i>
<code>code</code>
<a href="url">link</a>
<pre>preformatted</pre>
```

## Troubleshooting

### Bot Not Responding

**Check webhook status:**
```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

**Common issues:**
- Webhook URL not registered → Run setup script
- Bot token not configured → Check secrets.yaml
- Server not running → Check Render logs
- Network error → Check Render deployment status

**View logs:**
```bash
# In Render dashboard
grep -i "telegram" logs/output.log
```

### Webhook Verification Failed

**Symptoms:** Webhook receives messages but rejects them

**Solutions:**
1. Check webhook secret matches in both places
2. Verify signature validation in WebhookManager
3. Check `X-Telegram-Bot-Api-Secret-Token` header

### Multiple Active Chats

**Issue:** Tool sends to wrong chat

**Solution:** Specify `chat_id` parameter explicitly:
```python
send_telegram_message(
    message="Important alert",
    chat_id=123456789  # Specific chat
)
```

### Message Too Long

**Limit:** 4096 characters per Telegram message

**Solution:** Messages are automatically truncated with "..." suffix

**Better approach:** Split into multiple messages:
```python
# In tool implementation
if len(message) > 4000:
    # Send summary first
    send_telegram_message(message=summary)
    # Send details in separate message
    send_telegram_message(message=details)
```

## Security Considerations

### Token Management

- ✅ Store bot token in `config/secrets.yaml` (gitignored)
- ✅ Use environment variables on Render dashboard
- ❌ Never commit tokens to git
- 🔄 Rotate token if exposed (via @BotFather: `/token`)

### Webhook Verification

- Use `webhook_secret_token` for signature verification
- WebhookManager validates `X-Telegram-Bot-Api-Secret-Token` header
- All verification failures are logged

### Access Control (Future)

Currently **open access** (any Telegram user can message bot).

**Phase 2 features:**
- User whitelist (only allowed user_ids can message)
- Admin approval workflow
- Rate limiting per user
- Blocked user management

## Monitoring & Analytics

### Key Metrics

Track in Render logs:
- Webhook requests per minute
- Response latency (webhook → response sent)
- Active chat count
- Failed message sends
- Tool execution frequency

### Log Events

```python
# Important events logged:
logger.info(f"Telegram chat registered: chat_id={chat_id}, username=@{username}")
logger.info(f"Message received from chat {chat_id}: {text[:50]}...")
logger.info(f"Response sent to chat {chat_id}: {response[:50]}...")
logger.error(f"Failed to send Telegram message: {error}")
```

### Statistics API

```python
# Get chat statistics
from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository

chat_repo = TelegramChatRepository()
stats = chat_repo.get_stats()
# {
#   "total_chats": 5,
#   "active_chats": 3,
#   "unique_users": 4
# }
```

## Development & Testing

### Local Testing (Without Deployment)

1. **Start Brain locally:**
   ```bash
   $env:PYTHONPATH="."; python src/remote/server.py
   ```

2. **Use ngrok for webhook:**
   ```bash
   ngrok http 8767
   ```

3. **Register webhook:**
   ```bash
   python scripts/setup_telegram_webhook.py \
       --url https://abc123.ngrok.io/webhooks/telegram
   ```

4. **Test via Telegram app**

### Unit Testing

```python
# Test Telegram client
from src.remote.telegram.telegram_client import TelegramClient

async def test_send_message():
    client = TelegramClient(bot_token="test_token")
    result = await client.send_message(
        chat_id=123456789,
        text="Test message"
    )
    assert result["message_id"]
```

### Integration Testing

```python
# Test full flow
from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository

def test_session_key_mapping():
    repo = TelegramChatRepository()
    session_key = repo.create_or_update_chat(
        chat_id=123456789,
        user_id=987654321,
        username="testuser"
    )
    assert session_key == "main:987654321:telegram:123456789"

    retrieved_chat_id = repo.get_chat_id(session_key)
    assert retrieved_chat_id == 123456789
```

## API Reference

### TelegramClient

```python
from src.remote.telegram import get_telegram_client

client = get_telegram_client()

# Send message
await client.send_message(
    chat_id=123456789,
    text="Hello from Chad!",
    parse_mode="HTML"
)

# Edit message
await client.edit_message(
    chat_id=123456789,
    message_id=42,
    text="Updated text"
)

# Verify bot
bot_info = await client.get_me()
print(f"Bot: @{bot_info['username']}")
```

### TelegramChatRepository

```python
from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository

repo = TelegramChatRepository()

# Create/update chat
session_key = repo.create_or_update_chat(
    chat_id=123456789,
    user_id=987654321,
    username="john_doe",
    first_name="John"
)

# Get session key
session_key = repo.get_session_key(chat_id=123456789)

# Get chat details
chat = repo.get_chat(chat_id=123456789)
print(f"User: {chat.username}, Last message: {chat.last_message_at}")

# Get statistics
stats = repo.get_stats()
```

## FAQ

**Q: Can multiple users message the same bot?**
A: Yes! Each user gets their own isolated conversation with separate memory.

**Q: Does the bot work in group chats?**
A: Phase 1 supports only private chats. Group chat support is planned for Phase 2.

**Q: Can I send images or files?**
A: Phase 1 supports text only. Media support is planned for Phase 2.

**Q: How do I disable Telegram integration?**
A: Delete the webhook: `python scripts/setup_telegram_webhook.py --delete`

**Q: Can I use multiple bots?**
A: Currently only one bot token is supported. Multi-bot support would require code changes.

**Q: What happens if the server is down?**
A: Telegram queues messages for 24 hours. When server comes back online, it processes queued messages.

**Q: How do I reset a conversation?**
A: Delete the memory database: `rm data/memory/main/{user_id}/telegram_{chat_id}.db`

## Related Documentation

- **Cloud Deployment:** `docs/CHAD_REMOTE.md`
- **Webhook Setup:** `docs/WEBHOOK_SETUP_GUIDE.md`
- **Session Keys:** `src/core/intelligence/session_key_manager.py`
- **Tools:** `docs/TOOLS_ORGANIZATION.md`
- **Architecture:** `docs/features/TELEGRAM_INTEGRATION.md`

## Contributing

Want to add features? Check the plan in plan mode transcript:
- Phase 2: Streaming responses, buttons, media
- Security: User whitelist, rate limiting
- Analytics: Dashboard, metrics tracking

## Support

Issues? Questions?
- Check logs in Render dashboard
- Review webhook info via Telegram API
- Check `data/telegram_chats.db` for chat mappings
- Test webhook locally with ngrok
