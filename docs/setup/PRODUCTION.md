# Running Chad in Production

This is how Chad runs as a real, always-on product on your PC — so you *use* it
daily (it learns about you, runs heartbeats/crons, receives webhooks) while you
still develop it on the side and ship updates with one click.

---

## The mental model

```
┌─ Your PC (left on; a laptop is cheapest for power) ─────────────────┐
│                                                                     │
│   PRODUCTION                              DEVELOPMENT (when working) │
│   ───────────                            ────────────────────────── │
│   ChadBrain (master)   ── port 8767      Dev Brain (any branch) ─ 8788
│     ▲  supervised, 24/7, daemons ON        ▲  throwaway, daemons OFF │
│     │  auto-restart on crash/update        │                        │
│   Prod GUI ── ws://localhost:8767/ws     Dev GUI ── ws://…:8788/ws  │
│   (desktop "Chad" shortcut)              (desktop "Chad Dev" shortcut) │
└─────────────────────────────────────────────────────────────────────┘
```

**Prod and dev never collide** because they run on different ports (8767 vs 8788).
You can develop against the dev Brain while the prod Brain keeps living its life.

### For AI coding agents

The production Brain on **8767** is **always-on and sacred**. Agents must **connect only**
(evals, benchmarks, trace probes) — **never** `Stop-Process`, `taskkill`, or spawn a competing
`server.py` on `:8767`. If health check fails, ask Matthew; do not attempt to fix it. See
`.agent/rules/prod-brain-server.md`.

**`master` is sacred.** It's what production runs. You develop on feature branches,
test against the dev Brain, merge to `dev`, then fast-forward `dev → master` when
solid. Production picks up `master` whenever *you* click **Update**.

---

## Why a simple graceful restart is the right "professional" answer

Big always-on services avoid downtime with blue-green / rolling deploys — running
the new version alongside the old and flipping traffic. That machinery exists to
never drop a request across **thousands of concurrent users**.

You have **one user** and **all durable state lives in Supabase** (memory, learning,
RAG, chat history). So a restart loses *nothing*. And the GUI (`src/remote/limb.py`)
already auto-reconnects with exponential backoff. That means the right-sized,
genuinely-professional pattern for you is:

> **supervised process + graceful restart + auto-reconnecting client.**

An update is: pull `master` → restart the Brain (~5s) → GUI reconnects on its own.
No data lost, no manual steps. Building blue-green here would be over-engineering.

---

## One-time setup

From the repo root, in a normal PowerShell window (no admin needed):

```powershell
.\scripts\prod\install_prod.ps1
```

This:
1. Adds a **Startup-folder entry** (`%APPDATA%\…\Startup\ChadBrain.lnk`) that launches
   the supervisor hidden at logon — so prod is up whenever you're logged in. No admin
   needed (unlike a system scheduled task). The supervisor keeps the Brain alive and
   restarts it on exit; a named mutex guarantees only one supervisor ever runs.
2. Creates desktop shortcuts: **Chad** (prod GUI, an app-like windowless launcher with
   a custom icon — no console flash) and **Chad Dev** (one click starts Vite +
   dev Brain (8788) + dev GUI, also windowless — see "Developing alongside
   production" below).
3. Starts the Brain immediately (no need to log out/in), unless one's already serving 8767.
4. Prints recommended power settings.

> First boot takes ~25s to full readiness (the GUI's health check passes at ~3s when
> uvicorn binds; tool/embedding warmup finishes in the background). State is in Supabase,
> so this only matters on a cold start, not per-request.

Recommended power settings (keep the Brain awake, let the monitor sleep):

```powershell
powercfg /change standby-timeout-ac 0     # never sleep on AC
powercfg /change monitor-timeout-ac 10    # display off after 10 min
```

**Prereqs**: the prod box needs the env/secrets the Brain already uses
(`CHAD_SERVER_TOKEN`, `ANTHROPIC_API_KEY`, Supabase keys, Telegram token, etc.).
These load the same way they do today (`.env` / config / secrets). The supervisor
sets `CHAD_MODE=cloud`, `PORT=8767`. Daemons (cron/heartbeat/webhooks) are governed
by `daemons.enabled`/`daemons.<name>` in `config/preferences.yaml` (Settings →
Automations writes there) — the supervisor deliberately does not force
`CHAD_DAEMONS_ENABLED`, since that env var overrides the config file and would
silently ignore the in-GUI toggle (#473). Set it explicitly only for a one-off
override.

---

## Daily use

- Open **Chad** on your desktop. It connects to the always-on Brain. That's it.
- If the Brain is briefly down (e.g. mid-update), the GUI shows "reconnecting"
  and recovers on its own.

---

## Shipping an update to production

1. Develop on a feature branch; test against the **dev** Brain (desktop **Chad Dev** shortcut).
2. Merge into `dev`, then fast-forward into `master`:
   ```powershell
   git checkout master
   git merge --ff-only origin/dev
   git push origin master
   ```
3. In the prod GUI: **Settings → System → Update Chad.**

What the button does (calls the Brain's authenticated `POST /api/update`):
- `git fetch` + compares local `HEAD` to `origin/master`; if equal, says
  "Already up to date" and does nothing.
- Otherwise `git reset --hard origin/master`.
- Reinstalls Python deps **only if** `requirements.txt` changed.
- Rebuilds the web client **only if** `src/clients/web/**` changed.
- Triggers a **graceful** uvicorn shutdown; the supervisor relaunches the Brain
  on the new code; the GUI auto-reconnects.

Prefer a script over the button? Same effect from a shell on the prod box:
`git pull` then let the supervisor restart (or just click Update).

---

## Developing alongside production

Double-click **Chad Dev** on your desktop. One click, no console flash:

1. Starts the **Vite dev server** (`npm run dev`) — UI-only changes hot-reload
   automatically, no restart needed.
2. Starts a throwaway **dev Brain** on **8788** (daemons OFF), running whatever
   code is currently on disk (including uncommitted edits).
3. Opens the **dev GUI** pointed at the dev Brain + Vite.

Prod keeps running untouched on 8767.

**"Updating" dev** (picking up new server/backend code, or new branch you just
checked out): just **re-click the Chad Dev shortcut**. It stops the previous
Vite/Brain/GUI trio and relaunches all 3 with whatever's on disk now — no git
pull involved, since this is your active working tree. UI-only edits don't need
this at all (Vite HMR picks them up live); only Python/server changes do.

Logs land in `data/dev_logs/` (`vite_YYYYMMDD.log`, `brain_YYYYMMDD.log` +
matching `.err.log` files). Equivalent from a shell:
```powershell
.\scripts\prod\launch_dev.ps1   # start/restart the dev stack
.\scripts\prod\stop_dev_stack.ps1   # stop it without relaunching
```

---

## Operations

| Task | How |
| --- | --- |
| See Brain logs | `data/prod_logs/brain_YYYYMMDD.log` (stdout), `brain_YYYYMMDD.err.log` (stderr/uvicorn) |
| See supervisor log | `data/prod_logs/supervisor_YYYYMMDD.log` (launches, exits, restarts) |
| Restart prod now | Settings → System → Update (or kill the python; supervisor relaunches in ~10s) |
| Stop prod entirely | `.\scripts\prod\uninstall_prod.ps1` (removes autostart + shortcuts, stops Brain) |
| Check autostart | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ChadBrain.lnk` |
| Is it up? | `curl http://localhost:8767/health` → `ok` |

> **Process shape:** you'll see the supervisor (a hidden PowerShell) → a tiny ~4MB
> python launcher stub → the real ~3GB Brain that owns port 8767. Only the real Brain
> runs daemons; the stub is a Windows spawn artifact and is harmless. The supervisor's
> mutex prevents a second supervisor (which would fight over the port).

### Crash-loop protection
If the Brain exits more than 5 times in 60s (e.g. a bad deploy), the supervisor
backs off 30s before retrying instead of pinning the CPU. Check the prod log to
see what's failing, fix on a branch, ship via Update.

### Rolling back a bad update
```powershell
git checkout master
git reset --hard <last-good-sha>
git push --force-with-lease origin master
```
Then click **Update** again (it resets prod to match `master`).

---

## Going remote later (optional)

This setup is PC-local: prod is reachable while your PC is on. If you later want
to reach Chad from your phone or away from your PC, add a **Cloudflare Tunnel**
(or ngrok) in front of port 8767 and point a remote GUI at the tunnel URL via
`CHAD_SERVER_URL`. No rearchitecting required — the GUI only needs a URL.

---

## Files

| File | Purpose |
| --- | --- |
| `scripts/prod/chad_supervisor.ps1` | Keeps the Brain alive; restarts on exit; crash-loop backoff; single-instance mutex; OS-level log redirect |
| `scripts/prod/chad_supervisor_launch.vbs` | Hidden launcher for the supervisor (used by the Startup-folder entry) |
| `scripts/prod/install_prod.ps1` | One-time: Startup-folder autostart + app shortcuts + start |
| `scripts/prod/uninstall_prod.ps1` | Stop prod + remove autostart/shortcuts |
| `scripts/prod/launch_prod_gui.vbs` | App-like windowless GUI launcher (the "Chad" desktop icon) |
| `scripts/prod/launch_prod_gui.ps1` | Open the prod GUI from a shell (→ 8767) |
| `scripts/prod/chad_dev_launch.vbs` | Hidden launcher for the dev stack (the "Chad Dev" desktop icon) |
| `scripts/prod/launch_dev.ps1` | One-click dev stack: Vite + dev Brain (8788) + dev GUI; re-run to stop+relaunch (the dev "update") |
| `scripts/prod/stop_dev_stack.ps1` | Tree-kills the dev stack's tracked PIDs (+ port fallback) |
| `scripts/prod/chad.ico` | App icon for the desktop shortcuts |
| `src/remote/endpoints.py` `_update_endpoint` | `POST /api/update` (auth'd) |
| `src/remote/server.py` `request_restart()` | Graceful uvicorn shutdown for restart |
| `src/clients/desktop/api/system.py` `update_chad()` | GUI → Brain update bridge |
| Settings → System tab | The **Update Chad** button |
