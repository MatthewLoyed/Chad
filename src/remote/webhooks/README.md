# Webhooks System

Push notification system for external service integrations.

## Architecture

```
External Service → Starlette Route → WebhookManager → Handler → Orchestrator → Response
```

### Components

1. **WebhookManager** (`webhook_manager.py`)
   - Signature verification (HMAC, tokens)
   - Replay attack prevention
   - Event type detection
   - Handler registration and execution

2. **Starlette Routes** (`src/remote/server.py`)
   - `/webhooks/gmail` - Gmail Pub/Sub push notifications
   - `/webhooks/telegram` - Telegram bot updates
   - `/webhooks/github` - GitHub repository events

3. **Handlers** (`src/remote/server.py::_register_webhook_handlers`)
   - Service-specific event processing
   - Orchestrator integration
   - Response formatting

## Flow Diagram

```
┌──────────────────┐
│ External Service │
│  (Gmail, etc)    │
└────────┬─────────┘
         │ HTTP POST
         ▼
┌──────────────────────┐
│  Starlette Route     │
│  /webhooks/{service} │
└────────┬─────────────┘
         │ JSON payload + headers
         ▼
┌──────────────────────┐
│  WebhookManager      │
│  - Verify signature  │
│  - Check replay      │
│  - Create event      │
└────────┬─────────────┘
         │ WebhookEvent
         ▼
┌──────────────────────┐
│  Service Handler     │
│  - Parse payload     │
│  - Extract data      │
└────────┬─────────────┘
         │ user_input
         ▼
┌──────────────────────┐
│  Orchestrator        │
│  - Start turn        │
│  - Execute tools     │
│  - Generate response │
└────────┬─────────────┘
         │ Turn result
         ▼
┌──────────────────────┐
│  Response (optional) │
│  - Send to service   │
│  - Notify user       │
└──────────────────────┘
```

## Security

### Gmail Pub/Sub
- **Method:** Google Cloud Pub/Sub subscription validation
- **Verification:** Trusted through Google's infrastructure
- **Optional:** Additional token in subscription config

### Telegram
- **Method:** Secret token
- **Header:** `X-Telegram-Bot-Api-Secret-Token`
- **Verification:** Token comparison

### GitHub
- **Method:** HMAC SHA-256 signature
- **Header:** `X-Hub-Signature-256`
- **Secret:** Configured in repository webhook settings
- **Algorithm:** `HMAC-SHA256(secret, payload)`

### Replay Attack Prevention
- Recent events tracked in memory (last 1000)
- Event ID format: `{service}:{timestamp}:{payload_hash}`
- Duplicate events rejected automatically

## Adding New Services

### 1. Add Starlette Route

In `server.py`:

```python
async def _webhook_myservice_endpoint(request):
    """Handle MyService webhooks."""
    try:
        payload = await request.json()
        headers = dict(request.headers)

        webhook_manager = get_webhook_manager()
        event = await webhook_manager.process_webhook("myservice", payload, headers)

        if event:
            return JSONResponse({"status": "success"})
        else:
            return JSONResponse({"status": "rejected"}, status_code=400)
    except Exception as e:
        logger.error(f"MyService webhook error: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

# Add to routes list in _make_starlette_app()
Route("/webhooks/myservice", _webhook_myservice_endpoint, methods=["POST"])
```

### 2. Add Signature Verification

In `webhook_manager.py`:

```python
def _verify_myservice_signature(self, event: WebhookEvent) -> bool:
    """Verify MyService webhook signature."""
    signature = event.headers.get("x-myservice-signature", "")
    secret = self._secrets.get("myservice", "").encode()

    if not secret:
        logger.warning("No MyService webhook secret configured")
        return False

    # Compute signature based on service's algorithm
    payload_bytes = json.dumps(event.payload).encode()
    computed = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()

    return hmac.compare_digest(computed, signature)

# Add to _verify_signature() method
def _verify_signature(self, event: WebhookEvent) -> bool:
    # ... existing services ...
    elif service == "myservice":
        return self._verify_myservice_signature(event)
```

### 3. Register Handler

In `server.py::_register_webhook_handlers()`:

```python
async def handle_myservice_webhook(event):
    """Process MyService event."""
    logger.info("Processing MyService webhook event")

    payload = event.payload

    # Extract relevant data
    data = payload.get("data", {})
    event_type = payload.get("type", "unknown")

    # Process through orchestrator
    if self.session_manager and hasattr(self.session_manager, 'conversation_orchestrator'):
        orchestrator = self.session_manager.conversation_orchestrator

        from src.core.orchestration import TurnInitiator
        await orchestrator.start_turn(
            user_input=f"Process MyService event: {event_type}",
            category="autonomous",
            initiator=TurnInitiator.WEBHOOK,
            intent="myservice_event"
        )

# Register
webhook_manager.register_handler("myservice", handle_myservice_webhook)
```

### 4. Add Secret Configuration

In `config/secrets.yaml`:

```yaml
MYSERVICE_WEBHOOK_SECRET: "your-secret-here"
```

### 5. Document Setup

Add setup instructions to `docs/WEBHOOK_SETUP_GUIDE.md`.

## Event Types

### Gmail
- **Event:** `new_email`
- **Trigger:** New email in watched mailbox
- **Payload:** Base64-encoded Pub/Sub message with `emailAddress` and `historyId`

### Telegram
- **Events:** `message`, `callback`, `inline_query`
- **Trigger:** User sends message, clicks button, or uses inline query
- **Payload:** Telegram Update object

### GitHub
- **Events:** `push`, `pull_request`, `issues`, `ping`, etc.
- **Trigger:** Repository event based on webhook configuration
- **Payload:** GitHub event-specific payload
- **Header:** `X-GitHub-Event` contains event type

## Testing

### Manual Testing

```bash
# Start server
python -m src.remote.server

# Test webhooks (in another terminal)
python tests/test_webhooks_manual.py --all
```

### Using ngrok for Local Testing

```bash
# Start Chad server
python -m src.remote.server

# Start ngrok (in another terminal)
ngrok http 8767

# Use ngrok URL for webhook configuration
# Example: https://abc123.ngrok.io/webhooks/gmail
```

### curl Examples

```bash
# Gmail webhook
curl -X POST http://localhost:8767/webhooks/gmail \
  -H "Content-Type: application/json" \
  -d '{"message": {"data": "eyJlbWFpbEFkZHJlc3MiOiJ1c2VyQGV4YW1wbGUuY29tIiwiaGlzdG9yeUlkIjoxMjM0NTZ9"}}'

# Telegram webhook
curl -X POST http://localhost:8767/webhooks/telegram \
  -H "Content-Type: application/json" \
  -d '{"message": {"text": "Hello", "chat": {"id": 123}, "from": {"username": "test"}}}'

# GitHub webhook
curl -X POST http://localhost:8767/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: ping" \
  -d '{"zen": "Keep it simple."}'
```

## Monitoring

### Logs

Watch webhook activity:

```bash
tail -f logs/chad.log | grep -i webhook
```

Expected log entries:
```
INFO | WebhookManager | Processing webhook from: gmail
INFO | WebhookManager | Successfully processed webhook from: gmail
INFO | ChadServer | New email notification for user@example.com
```

### Metrics (Future)

Track webhook statistics:
- Total webhooks received per service
- Verification success/failure rates
- Average processing time
- Error rates

## Troubleshooting

### Webhook Not Received

1. Check server is running: `curl http://localhost:8767/health`
2. Check route exists: Review `server.py::_make_starlette_app()`
3. Check external service configuration
4. Check firewall/network settings

### Signature Verification Failed

1. Verify secret matches external service configuration
2. Check header is present and formatted correctly
3. Review service-specific signature algorithm
4. Add debug logging to verification method

### Handler Not Executing

1. Verify handler is registered: Check startup logs for "Webhook handlers registered"
2. Check orchestrator is initialized: Look for "Headless Brain is ONLINE"
3. Add debug logging to handler
4. Check for exceptions in logs

### Replay Attack False Positives

- Event ID collision (very rare)
- Clear recent events cache (restart server)
- Increase `_max_recent` if needed (currently 1000)

## Best Practices

1. **Always verify signatures** in production
2. **Use HTTPS** for all webhook endpoints
3. **Implement rate limiting** to prevent abuse
4. **Log all webhook events** for debugging
5. **Handle failures gracefully** - return appropriate HTTP status codes
6. **Test with sample payloads** before production deployment
7. **Document webhook URLs** and required configuration
8. **Monitor webhook health** and set up alerts for failures

## Future Enhancements

- [ ] Webhook queue for async processing
- [ ] Retry mechanism for failed webhooks
- [ ] Webhook analytics dashboard
- [ ] Rate limiting per service
- [ ] IP whitelisting
- [ ] Webhook health checks
- [ ] Automatic secret rotation
- [ ] Multi-instance webhook distribution

## References

- Gmail Pub/Sub: https://developers.google.com/gmail/api/guides/push
- Telegram Bots: https://core.telegram.org/bots/webhooks
- GitHub Webhooks: https://docs.github.com/webhooks
- HMAC Security: https://en.wikipedia.org/wiki/HMAC
