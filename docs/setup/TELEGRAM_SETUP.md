# Telegram Integration - Quick Setup Guide

> **Unified guide:** See [CONNECTORS.md](CONNECTORS.md) for the full connector catalog. Use **Settings → MCP Servers → Connectors → Telegram** for the in-app wizard.

## How it connects (simple)

Telegram never talks to LM Studio or your files directly. Your phone talks to
**Telegram's cloud**, which POSTs to a public HTTPS URL that tunnels into **your**
Brain on this PC (`/webhooks/telegram` → session map → orchestrator → Bot API reply).

```
You (phone) → Telegram cloud → tunnel (Tailscale / ngrok) → Brain :8767
                                                      ↓
                                         chat_id → session_key
                                                      ↓
                                    category=contact (9B front desk)
                                                      ↓
              greeting / photo Q&A → personal reply | task → personal ack
                                                      ↓
              (on escalate) category=telegram_agent (pocket remote tools)
                                                      ↓
                         new sendMessage (+ documents when produced)
```

Your Brain stays on your machine. Telegram is a personal remote control, not a
hosted Chad cloud.

## Telegram vs PWA

| | **Telegram** | **PWA / desktop** |
|---|---|---|
| Job | Pocket remote while you’re out | Full Chad cockpit |
| Front desk | 9B contact (greetings, photo Q&A) | N/A (you’re already in the app) |
| Tool work | `telegram_agent` — fixed menu (search, mail, calendar, memory, places-as-links, file exports) | Full supervisor + specialist delegates |
| Out of scope | Coding, browser automation, desktop control, GUI cards, OAuth reconnect | Those live here |

Photos stay on the contact 9B lane (multimodal). Escalate only when tools are needed.

## Contact front desk (9B)

Telegram DMs enter as `category="contact"` on the **qwen/qwen3.5-9b** model
(`local_models.category_models.contact`), not the full Gemma supervisor path.

| You send | What happens |
|---|---|
| Greeting / small-talk (`heyyy`, wsp, …) | 9B replies in character. No agent turn. |
| Photo (with or without caption) | Downloaded via `getFile`, passed as `content_blocks`. 9B answers photo questions itself when no tools are needed. |
| Real task (email, calendar, search, …) | 9B sends a short personal “I’m on it…” as its own message, then escalates to **`telegram_agent`** (Gemma + Telegram-only tools). The answer is a **new** message (send-only). |

Typing stays on for the desk + agent span. Desktop GUI is unchanged (still auto/agent).

Allowlist + webhook `secret_token` behavior is unchanged.

## Security (open-source posture)

| Layer | What it does |
|---|---|
| Bot token | Only the token holder can register the webhook / send as the bot. Keep it in `config/secrets.yaml` (gitignored). |
| Webhook `secret_token` | Telegram sends `X-Telegram-Bot-Api-Secret-Token`; Chad rejects mismatches when configured. |
| **Allowlist (required)** | Only `allowed_user_ids` / `allowed_chat_ids` can start turns. **Empty = reject everyone** (fail closed). |
| Tunnel | Prefer Tailscale / private tunnel for day-to-day. ngrok is fine for local testing. |

Open-source story: **your Brain stays local; Telegram is a locked personal remote (token + allowlist + tunnel), not a public chatbot.**

## TL;DR — Get started

### 1. Create bot
1. Open Telegram → `@BotFather` → `/newbot`
2. Copy the token (`123456789:ABC…`)

### 2. Configure secrets
```yaml
# config/secrets.yaml
telegram:
  bot_token: "paste-your-token-here"
  webhook_secret_token: "paste-a-long-random-string"  # openssl rand -hex 32
  allowed_user_ids: [YOUR_TELEGRAM_USER_ID]           # @userinfobot
  allowed_chat_ids: [YOUR_TELEGRAM_USER_ID]           # same for DMs
  pair_on_first_message: false
```

First-time convenience: set `pair_on_first_message: true` with empty allowlists —
the first person who DMs the bot is written into the allowlist, then the flag clears.
Leave it `false` for normal use.

Also enable the connector (Settings → Connectors → Telegram), or remove `telegram`
from `disabled_native_connectors` in `config/preferences.yaml`.

### 3. Expose the Brain + register webhook
```bash
# Dev: public HTTPS to localhost:8767 (ngrok example)
ngrok http 8767

# Register (reads bot token + webhook_secret_token from secrets.yaml)
python -m src.scripts.setup_telegram_webhook \
    --url https://YOUR-HOST/webhooks/telegram
```

Prefer Tailscale or Cloudflare Tunnel for anything beyond a short test — see
[PRODUCTION.md](PRODUCTION.md) and [SECURITY_POSTURE.md](../SECURITY_POSTURE.md).

### 4. Test
Open Telegram → your bot → send `heyyy`.

You should get a personal 9B reply (e.g. using your first name) with **no** long
agent delay. Then try a real task like “what’s on my calendar” — you should see
a short personal ack as its own message, then a **new** message with the real answer
(no edit-in-place).

**Stop an in-flight turn:** send `/stop` (or `/cancel`). Same pattern as OpenClaw —
it bypasses the normal turn queue so it can interrupt work that is already running.
You should get `Stopped.` (or `Nothing running to stop.`).

---

## What you get

- Bidirectional messaging (each chat gets its own session)
- 9B contact front desk (personal greetings; escalate → Gemma for real work)
- Autonomous notifications (heartbeat/cron) when configured
- Local chat↔session routing even without Supabase (`telegram_chats` via LocalPostgrestShim)
---

## Troubleshooting

**Bot not responding?**
```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```
1. Webhook not registered → run setup script  
2. Token invalid → BotFather → API Token (401 = revoked)  
3. Allowlist empty / wrong ID → rejected with a private-bot notice  
4. Secret mismatch → Brain logs `secret_token mismatch`  
5. Tunnel down → Telegram `last_error_message` on getWebhookInfo  

**Unauthorized notice but you're the owner?**  
Add your numeric user id to `telegram.allowed_user_ids` (and chat id for groups),
restart the Brain.

---

## Full documentation

- Connectors catalog: [CONNECTORS.md](CONNECTORS.md)
- Architecture notes: `docs/features/TELEGRAM_INTEGRATION.md`
- Webhook details: [WEBHOOK_SETUP_GUIDE.md](WEBHOOK_SETUP_GUIDE.md)
- Security posture: [SECURITY_POSTURE.md](../SECURITY_POSTURE.md)
