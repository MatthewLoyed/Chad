# Webhook Setup Guide

> **Unified guide:** See [CONNECTORS.md](CONNECTORS.md) for Gmail and Telegram connector setup. This doc covers webhook endpoints for push notifications.

Chad supports push notifications from external services via webhooks. This allows external services to notify Chad immediately when events occur, rather than polling periodically.

---

## Supported Services

| Service | Webhook Path | Events | Status |
|---------|--------------|--------|--------|
| **Gmail** | `/webhooks/gmail` | New email notifications | Ready |
| **Telegram** | `/webhooks/telegram` | Bot messages, callbacks | Ready |
| **GitHub** | `/webhooks/github` | Push, PR, issues | Ready |

---

## General Architecture

### How Webhooks Work

```
External Service → POST /webhooks/{service} → WebhookManager → Handler → Orchestrator
```

1. **External service** sends HTTP POST to your Chad server
2. **WebhookManager** verifies signature (HMAC, token, etc.)
3. **Handler** processes event and triggers orchestrator
4. **Orchestrator** performs action (check email, respond to message, etc.)

### Security

All webhooks implement security verification:
- **Gmail:** Google Pub/Sub authentication (via subscription validation)
- **Telegram:** Secret token in `X-Telegram-Bot-Api-Secret-Token` header
- **GitHub:** HMAC SHA-256 signature in `X-Hub-Signature-256` header

Replay attack prevention: Recent events are tracked and duplicate events are rejected.

---

## Gmail Pub/Sub Setup

Gmail uses Google Cloud Pub/Sub to send push notifications when new emails arrive.

### Prerequisites

- Google Cloud Project with Gmail API enabled
- Service account with Pub/Sub permissions
- Chad server with public HTTPS endpoint (ngrok, Render, etc.)

### Step 1: Create Pub/Sub Topic

```bash
# Using gcloud CLI
gcloud pubsub topics create gmail-notifications

# Grant Gmail permission to publish
gcloud pubsub topics add-iam-policy-binding gmail-notifications \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
  --role=roles/pubsub.publisher
```

### Step 2: Create Pub/Sub Subscription

```bash
# Create push subscription pointing to your Chad server
gcloud pubsub subscriptions create gmail-push-to-chad \
  --topic=gmail-notifications \
  --push-endpoint=https://your-chad-server.com/webhooks/gmail \
  --push-auth-service-account=your-service-account@your-project.iam.gserviceaccount.com
```

**Important:** Replace `https://your-chad-server.com` with your actual server URL.

### Step 3: Set Up Gmail Watch

```python
# In Python (one-time setup)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

creds = Credentials.from_authorized_user_file('token.json')
service = build('gmail', 'v1', credentials=creds)

request = {
    'labelIds': ['INBOX'],
    'topicName': 'projects/your-project-id/topics/gmail-notifications'
}

result = service.users().watch(userId='me', body=request).execute()
print(f"Watch set up! Expires at: {result['expiration']}")
```

**Note:** Gmail watch expires after 7 days. You need to renew it periodically (can be done via cron job).

### Step 4: Configure Chad Secrets

```yaml
# config/secrets.yaml
GMAIL_WEBHOOK_SECRET: "your-secret-token"  # Optional, for extra verification
```

### Testing

Send yourself an email and check Chad logs:

```bash
tail -f logs/chad.log | grep -i webhook
```

You should see:
```
INFO | WebhookManager | Processing webhook from: gmail
INFO | ChadServer | New email notification for user@example.com, history_id: 123456
```

---

## Telegram Bot Webhook Setup

Telegram bots can receive updates via webhooks instead of polling.

### Prerequisites

- Telegram bot token (from @BotFather)
- Chad server with public HTTPS endpoint

### Step 1: Create Telegram Bot

1. Open Telegram and message @BotFather
2. Send `/newbot` and follow prompts
3. Save the bot token (e.g., `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

### Step 2: Set Webhook URL

```bash
# Using curl
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-chad-server.com/webhooks/telegram",
    "secret_token": "your-secret-token-here",
    "allowed_updates": ["message", "callback_query"]
  }'
```

**Important:**
- Replace `<YOUR_BOT_TOKEN>` with your actual bot token
- Replace `https://your-chad-server.com` with your server URL
- Generate a random secret token (e.g., `openssl rand -hex 32`)

### Step 3: Configure Chad Secrets

```yaml
# config/secrets.yaml
TELEGRAM_BOT_TOKEN: "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
telegram_webhook_token: "your-secret-token-here"  # Must match Step 2
```

### Step 4: Verify Webhook

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

Should return:
```json
{
  "ok": true,
  "result": {
    "url": "https://your-chad-server.com/webhooks/telegram",
    "has_custom_certificate": false,
    "pending_update_count": 0
  }
}
```

### Testing

1. Send a message to your bot in Telegram
2. Check Chad logs:

```bash
tail -f logs/chad.log | grep -i telegram
```

You should see:
```
INFO | WebhookManager | Processing webhook from: telegram
INFO | ChadServer | Telegram message from username: Hello Chad!
```

---

## GitHub Webhook Setup

GitHub can send webhooks for repository events (push, PR, issues, etc.).

### Prerequisites

- GitHub repository with admin access
- Chad server with public HTTPS endpoint

### Step 1: Add Webhook in GitHub

1. Go to your repository settings
2. Navigate to **Settings → Webhooks → Add webhook**
3. Configure:
   - **Payload URL:** `https://your-chad-server.com/webhooks/github`
   - **Content type:** `application/json`
   - **Secret:** Generate a random secret (e.g., `openssl rand -hex 32`)
   - **Events:** Select events you want (push, pull_request, issues, etc.)
   - **Active:** Check this box
4. Click **Add webhook**

### Step 2: Configure Chad Secrets

```yaml
# config/secrets.yaml
GITHUB_WEBHOOK_SECRET: "your-secret-from-step-1"
```

### Step 3: Verify Webhook

After adding, GitHub will send a test ping. Check:
- GitHub webhook page should show green checkmark
- Chad logs should show:

```bash
tail -f logs/chad.log | grep -i github
```

```
INFO | WebhookManager | Processing webhook from: github
INFO | ChadServer | Processing GitHub webhook event: ping
```

### Testing

1. Push a commit to your repository
2. Check Chad logs for event processing

---

## Webhook Development & Testing

### Local Development with ngrok

Webhooks require public HTTPS endpoints. Use ngrok for local testing:

```bash
# Start Chad server
python -m src.remote.server

# In another terminal, start ngrok
ngrok http 8767

# Copy the HTTPS URL (e.g., https://abc123.ngrok.io)
# Use this URL when configuring webhooks
```

### Manual Testing

You can manually send webhooks using curl:

```bash
# Test Gmail webhook
curl -X POST http://localhost:8767/webhooks/gmail \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "data": "eyJlbWFpbEFkZHJlc3MiOiJ1c2VyQGV4YW1wbGUuY29tIiwiaGlzdG9yeUlkIjoxMjM0NTZ9",
      "messageId": "test-123",
      "publishTime": "2026-03-16T12:00:00Z"
    }
  }'

# Test Telegram webhook
curl -X POST http://localhost:8767/webhooks/telegram \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "message_id": 1,
      "from": {"id": 123, "username": "testuser"},
      "chat": {"id": 123, "type": "private"},
      "text": "Hello Chad!"
    }
  }'

# Test GitHub webhook (with signature)
# Note: Signature verification will fail without proper HMAC
curl -X POST http://localhost:8767/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: ping" \
  -d '{"zen": "Design for failure.", "hook_id": 123}'
```

---

## Webhook Handler Customization

Webhook handlers are defined in `src/remote/server.py` in the `_register_webhook_handlers` method.

### Example: Custom Gmail Handler

```python
async def handle_gmail_webhook(event):
    """Custom Gmail webhook handler."""
    # Decode Pub/Sub message
    message = event.payload.get("message", {})
    data_base64 = message.get("data", "")

    if data_base64:
        data = json.loads(base64.b64decode(data_base64).decode("utf-8"))

        # Custom logic: Only trigger for specific senders
        if "emailAddress" in data:
            # Check for important emails
            # Trigger orchestrator
            # Send notification
            pass
```

### Example: Telegram Response Handler

```python
async def handle_telegram_webhook(event):
    """Process Telegram message and respond."""
    message = event.payload.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")

    # Process through orchestrator
    orchestrator = self.session_manager.conversation_orchestrator
    response = await orchestrator.process_message(text)

    # Send response back to Telegram
    import aiohttp
    bot_token = ConfigManager.get_secret("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async with aiohttp.ClientSession() as session:
        await session.post(url, json={
            "chat_id": chat_id,
            "text": response
        })
```

---

## Troubleshooting

### Webhook Not Receiving Events

1. **Check server is running:**
   ```bash
   curl http://localhost:8767/health
   # Should return: ok
   ```

2. **Check server is publicly accessible:**
   ```bash
   curl https://your-chad-server.com/health
   # Should return: ok
   ```

3. **Check webhook configuration:**
   - Gmail: Verify Pub/Sub subscription is active
   - Telegram: Check `getWebhookInfo`
   - GitHub: Check webhook page for recent deliveries

4. **Check logs:**
   ```bash
   tail -f logs/chad.log | grep -i webhook
   ```

### Signature Verification Failing

1. **Check secret is configured:**
   ```bash
   # config/secrets.yaml should contain:
   GITHUB_WEBHOOK_SECRET: "your-secret"
   ```

2. **Verify secret matches:**
   - GitHub: Secret in repository webhook settings
   - Telegram: Secret token from `setWebhook`

3. **Check headers:**
   - GitHub: `X-Hub-Signature-256` header present
   - Telegram: `X-Telegram-Bot-Api-Secret-Token` header present

### Events Not Triggering Orchestrator

1. **Check handler is registered:**
   ```bash
   # Logs should show on startup:
   INFO | ChadServer | Webhook handlers registered
   ```

2. **Check orchestrator is initialized:**
   ```bash
   # Logs should show:
   INFO | ChadServer | Headless Brain is ONLINE and logic-ready
   ```

3. **Add debug logging:**
   ```python
   # In webhook handler
   logger.warning(f"DEBUG: Processing {event.service} event")
   ```

---

## Security Best Practices

1. **Always use HTTPS** - Webhooks should never use plain HTTP in production
2. **Verify signatures** - Always validate HMAC/token signatures
3. **Rate limiting** - Consider adding rate limits to webhook endpoints
4. **IP whitelisting** - Optional: Whitelist service IPs (Gmail, GitHub, etc.)
5. **Rotate secrets** - Periodically rotate webhook secrets
6. **Monitor logs** - Watch for suspicious webhook activity

---

## Advanced: Custom Webhook Services

You can add support for custom webhook services by:

1. **Add webhook route** in `server.py`:
   ```python
   Route("/webhooks/custom", _webhook_custom_endpoint, methods=["POST"])
   ```

2. **Create handler function**:
   ```python
   async def _webhook_custom_endpoint(request):
       payload = await request.json()
       headers = dict(request.headers)
       webhook_manager = get_webhook_manager()
       event = await webhook_manager.process_webhook("custom", payload, headers, verify=False)
       if event:
           return JSONResponse({"status": "success"})
       return JSONResponse({"status": "rejected"}, status_code=400)
   ```

3. **Register handler**:
   ```python
   async def handle_custom_webhook(event):
       # Your custom logic
       pass

   webhook_manager.register_handler("custom", handle_custom_webhook)
   ```

---

## Summary

| Service | Setup Complexity | Real-time | Recommended For |
|---------|------------------|-----------|-----------------|
| **Gmail** | High (Pub/Sub) | Yes | Urgent email notifications |
| **Telegram** | Low | Yes | Chat interface, commands |
| **GitHub** | Low | Yes | Repo monitoring, CI/CD |

**When to use webhooks vs heartbeat:**
- **Webhooks:** Real-time notifications (< 1 second latency)
- **Heartbeat:** Periodic checks (30 min intervals, batched)

**Example:**
- Use **Gmail webhook** for "notify me immediately when boss emails"
- Use **heartbeat** for "check if I have any important emails"

---

## Files Reference

- `src/remote/webhooks/webhook_manager.py` - Webhook processing and verification
- `src/remote/server.py` - Webhook routes and handlers
- `config/secrets.yaml` - Webhook secrets configuration
- `docs/WEBHOOK_SETUP_GUIDE.md` - This file
