# Security posture

_Last updated: 2026-10-21._

## Experimental single-user deployment

Chad is an alpha, not a security-certified product. Local storage and a personal
deployment reduce some risks, but tools, model mistakes, prompt injection, and
third-party dependencies can still expose or modify data. This document describes
the intended boundaries and known behavior, not a guarantee that leaks are impossible.

- **No public server.** Chad's Brain is a personal, single-user process that runs on
  your own PC. There's no hosted multi-tenant Chad service, no "Chad cloud" holding
  your credentials.
- **Single-user by design.** There's no user/tenant model to compromise — one person's
  Brain, one person's data, one machine.
- **Local data by default.** Chat history, memory, and persona all work with a
  zero-Supabase local-only fallback (`docs/setup/COLD_START.md`). Web searches,
  integrations, configured cloud models, optional tracing, and downloaded resources
  can communicate with external services.
- **Tunnel, not open port, for remote/phone access.** The PWA/phone flow whitelists
  your machine's [Tailscale](https://tailscale.com/) IP as a trusted origin
  (`src/remote/server.py`, `_tailscale_origins()`) — traffic to your phone rides
  Tailscale's private, authenticated overlay network. Chad does not open a port
  forward or ask you to expose anything to the public internet.
- **No community-skill supply chain.** Skills ship in this repo and are reviewed like
  any other code change. There's no marketplace where a third party can publish a
  skill that gets pulled into your install unreviewed.

## Threat model

**In scope / boundaries that require verification:**
- A process or device on your LAN, or a stranger on the internet, connecting to your
  Brain's WebSocket/REST port without your consent.
- Unexpected data access or outbound activity by tools, skills, or dependencies.
- Chat/memory data leaving your machine without an explicit opt-in (cloud model,
  Supabase project, or MCP integration you configured).

**Out of scope / not what this doc claims:**
- Malware or a compromised OS on the machine Chad runs on — if your Windows install is
  already compromised, no application-level auth saves you. Chad does not sandbox
  itself from its own host by default (the code agent runs in an E2B cloud sandbox;
  browser/UI-automation/system agents, when explicitly enabled, act with your own
  Windows-user permissions, same as any desktop app you run).
- Anything you deliberately expose — e.g., manually port-forwarding 8767 to the
  internet, or handing your `CHAD_SERVER_TOKEN` to someone.
- Third-party services you opt into (a cloud LLM provider, Supabase, Telegram, Gmail)
  have their own security posture; Chad inherits whatever data-handling policy you
  agreed to with them.

## Telegram as a personal remote (not a public bot)

Telegram is an **opt-in channel** that punches a controlled hole through the
local-first model: Telegram's cloud POSTs to a tunnel URL that reaches **your**
Brain. Chad is not a hosted multi-tenant chatbot.

Required controls for open-source installs:

1. **Bot token** in `config/secrets.yaml` (never commit it).
2. **Webhook `secret_token`** (`telegram.webhook_secret_token`) registered with
   Telegram and verified on `/webhooks/telegram`.
3. **Fail-closed allowlist** — `telegram.allowed_user_ids` /
   `telegram.allowed_chat_ids`. Empty lists reject all inbound turns. Optional
   one-shot `telegram.pair_on_first_message: true` seeds the allowlist from the
   first sender, then clears itself.
4. **Tunnel, not an open port** — prefer Tailscale / private tunnel for day-to-day
   phone access; ngrok is acceptable for short local testing only.

See `docs/setup/TELEGRAM_SETUP.md`.

## Default network surface (self-audit)

| Service | Default bind | Default port | Auth |
|---|---|---|---|
| Brain (WebSocket + REST, `src/remote/server.py`) | `0.0.0.0` (all interfaces) | `8767` | See below |
| MCP servers (`src/mcp_servers/*.py`) | stdio only | n/a | Not network-reachable |
| Local model backends (LM Studio / Ollama) | `127.0.0.1` (loopback) | `1234` / `11434` | Third-party process, not controlled by Chad; loopback-only by their own defaults |

The Brain binds `0.0.0.0` rather than loopback-only because a phone on the same
Tailscale network needs to reach it directly — but "listens on all interfaces" is not
the same as "accepts unauthenticated connections from all interfaces":

- **Same-machine callers (the paired desktop GUI) never need a token.** Loopback
  connections (`127.0.0.1`/`::1`/`localhost`) are recognized and authenticated
  implicitly — the token exists to gate *other machines*, not the local Brain↔GUI
  pairing (`src/remote/auth.py::is_loopback_host`).
- **Non-loopback callers must present `CHAD_SERVER_TOKEN`.** If none has been
  configured yet — true on a completely fresh install — the Brain generates a random
  token on first boot and persists it to `config/secrets.yaml`
  (`ChadServer.__init__` → `_generate_and_persist_server_token()`), so there is never
  a window where the port accepts unauthenticated remote connections. The generated
  token is logged once at boot so you can copy it into the PWA/phone pairing screen.
  Every non-loopback auth path (`verify_token`, `_check_update_token`, the command
  endpoint) fails closed if a token is somehow still missing, rather than allowing
  the connection.
- **CORS additionally restricts browser-origin requests** to `localhost`, your
  detected Tailscale IP, and any origin you list in `CHAD_ALLOWED_ORIGINS` — this is a
  secondary, browser-specific check on top of the token, not a substitute for it.

This was audited directly against the code while writing this doc (2026-07-10); an
earlier version of this logic had a real gap where an unconfigured token fell back to
allow-all for non-loopback callers, mirroring the exact OpenClaw failure mode
described above. It's fixed as of this doc — see `src/remote/server.py`,
`src/remote/auth.py`, and `src/remote/endpoints.py`.

## What's disabled by default

Beyond network exposure, Chad also ships several capabilities **off by default**
because they can act on your behalf and deserve deliberate opt-in — browser
automation (full DOM control of your logged-in Chrome), the system agent
(OS/hardware control), UI automation (mouse/keyboard/screen), and the communication
agent (Gmail/Slack/Telegram). See the capability table in the [README](../README.md)
and `config/config.yaml`'s `disabled_subagents`.

## Reporting a vulnerability

Found a real issue? Please don't open a public GitHub issue with exploit details —
email the maintainer directly (see repo contact) or open a private security advisory
via GitHub's "Report a vulnerability" flow on this repo.
