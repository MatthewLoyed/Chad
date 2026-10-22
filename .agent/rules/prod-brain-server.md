---
trigger: always_on
description: Production Brain (port 8767) is always-on — agents connect only; never kill or restart it.
globs: **/*
---

# Production Brain — connect only (port 8767)

Matthew runs Chad's **production Brain 24/7** via `scripts/prod/chad_supervisor.ps1`. It owns **port 8767** and serves the real product (daemons, heartbeats, webhooks, memory, Telegram).

## Hard rules for AI agents

1. **NEVER kill, stop, restart, or replace** any process bound to port **8767** — no `Stop-Process`, `taskkill`, `kill_chad_zombies.ps1` against the Brain, and no manual `server.py` on `:8767`.
2. **NEVER start** `src/remote/server.py` or `dev_server.py` on port **8767** to "test your changes". That fights the supervisor and can take prod offline.
3. **ALWAYS connect** to the live server for evals, benchmarks, trace probes, workflow tests, and integration checks:
   ```powershell
   Invoke-WebRequest http://localhost:8767/health -UseBasicParsing
   ```
   If health is not `ok`, **stop and ask the user** — do not attempt to fix by killing or spawning a server.
4. **Code you edit is not live** until the user ships it (Settings → Update, or their prod restart workflow). Live `--live` / `--workflow` evals exercise **whatever is currently running**, not uncommitted local edits.
5. **Isolated dev Brain** (when the user explicitly wants a throwaway stack) uses port **8788** via `scripts/prod/launch_dev.ps1` — never `:8767`.

## What is normal on prod

- **Two `server.py` processes** (supervisor parent + child that binds `:8767`) — intentional; do not "clean up the duplicate".
- Supervisor auto-restarts on crash; only the user controls deliberate prod restart (Update button, `uninstall_prod.ps1`, or killing the **supervisor parent tree** — never do this as an agent).

## Orphan cleanup scope

`kill_chad_zombies.ps1` targets **MCP prewarm zombies** (`npx`, `mcp-server`, etc.) — not the production Brain. Never use it to free port 8767.

See also: `docs/setup/PRODUCTION.md`, `CLAUDE.md` (Process cleanup / prod Brain section).
