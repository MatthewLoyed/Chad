# Process Cleanup (Zombie Prevention)

Chad spawns background subprocesses during development — MCP package prewarm (`npx`), the Hands OS daemon, Electron HUD, and uvicorn reload workers. If you stop processes abruptly (Task Manager, force-close terminal, crash), orphaned `cmd.exe`, `node.exe`, and `python.exe` children can accumulate.

> **Production Brain (port 8767):** This script targets MCP prewarm zombies only — **not** the 24/7
> supervised Brain. AI agents must **never** use it (or any kill command) to free `:8767`.
> See `.agent/rules/prod-brain-server.md`.

Run the cleanup script when needed after dev sessions — especially MCP zombie buildup.

---

## Quick cleanup (recommended)

From the project root:

```powershell
.\scripts\maintenance\kill_chad_zombies.ps1
```

Preview what would be killed without stopping anything:

```powershell
.\scripts\maintenance\kill_chad_zombies.ps1 -WhatIf
```

---

## When to run it

| Situation | Action |
|-----------|--------|
| End of a dev session | Run cleanup before closing your machine |
| Task Manager shows many `cmd` / `node` processes | Run cleanup |
| After force-killing Brain or GUI | Run cleanup |
| GUI feels sluggish / high CPU with Chad closed | Run cleanup |
| Weekly maintenance | Run cleanup once |

---

## What gets killed

| Target | Command-line match | Source |
|--------|-------------------|--------|
| MCP prewarm zombies | `npx ... --help`, `mcp-server`, `mcp-fetch`, `todoist-mcp` | Brain startup / uvicorn `--reload` |
| Hands daemon orphans | `hands.py` | GUI launches (one daemon should persist) |

**Not targeted:** Cursor IDE processes (e.g. `codegraph serve --mcp`), your Vite dev server (`npm run dev`), or other unrelated Node apps.

---

## Manual one-liners

If you prefer not to use the script:

```powershell
# MCP prewarm zombies
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'npx.*--help|mcp-server|mcp-fetch|todoist-mcp'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Orphaned Hands daemons
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'hands\.py'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

---

## Prevention (built into Chad)

Recent fixes reduce zombie accumulation:

- **MCP prewarm** — subprocesses are timed out and killed instead of fire-and-forget; disable with `CHAD_MCP_PREWARM=false`
- **Hands daemon** — singleton lock (`hands_daemon.lock`); new launches replace the old daemon
- **GUI shutdown** — Electron HUD is terminated on exit
- **Brain shutdown** — MCP registry cleanup on server stop
- **Dev server** — uvicorn process tree killed on Ctrl+C

Still run cleanup after crashes or force-kills — those bypass normal shutdown.

---

## Check process counts

```powershell
Write-Host "cmd:  $((Get-Process cmd -ErrorAction SilentlyContinue).Count)"
Write-Host "node: $((Get-Process node -ErrorAction SilentlyContinue).Count)"
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'hands\.py' } | Measure-Object | ForEach-Object { Write-Host "hands.py: $($_.Count)" }
```

Dozens of `cmd`/`node` with Chad closed is a sign you need cleanup.
