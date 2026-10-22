<#
.SYNOPSIS
    Production supervisor for the Chad Brain (headless server).

.DESCRIPTION
    Keeps the always-on production Brain alive on port 8767. Runs the server in a
    loop and relaunches it whenever it exits - whether from a crash or from the
    deliberate graceful restart triggered by the in-GUI "Update Chad" button
    (/api/update -> uvicorn should_exit -> process exit -> this loop relaunches
    with freshly pulled code).

    Crash-loop protection: if the server exits more than $MaxRestarts times within
    $WindowSeconds, the supervisor backs off for $BackoffSeconds before trying again,
    so a broken deploy can't pin the CPU.

    Graceful stop: create the file scripts/prod/.stop (or run uninstall_prod.ps1)
    and the loop exits after the current iteration.

.NOTES
    This script is registered to run at logon by install_prod.ps1. It runs in the
    user session (not as a SYSTEM service) on purpose, so the Brain has access to
    local models, the GPU, and audio hardware for voice mode.
#>

$ErrorActionPreference = "Stop"

# --- Resolve repo root (this script lives in <root>/scripts/prod) ---
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Server   = Join-Path $RepoRoot "src\remote\server.py"
$LogDir   = Join-Path $RepoRoot "data\prod_logs"
$StopFlag = Join-Path $PSScriptRoot ".stop"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("brain_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$ErrFile = Join-Path $LogDir ("brain_{0}.err.log" -f (Get-Date -Format "yyyyMMdd"))
$SupFile = Join-Path $LogDir ("supervisor_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $SupFile -Value $line
}

# --- Production environment ---
$env:PYTHONPATH            = "."
$env:CHAD_MODE             = "cloud"   # headless Brain (ConversationOrchestrator)
$env:PORT                  = "8767"    # canonical prod port
$env:PYTHONUNBUFFERED      = "1"       # flush logs promptly to the redirected files
if (-not $env:CHAD_LOG_LEVEL) { $env:CHAD_LOG_LEVEL = "INFO" }
# CHAD_DAEMONS_ENABLED (cron/heartbeat/webhooks) is intentionally NOT set here.
# The env var is an override that always wins over config/preferences.yaml (see
# ConfigManager/remote_factory/daemon_status precedence) — hardcoding it to "true"
# silently overrode the Settings -> Automations "enabled" preference even across a
# full supervisor restart (#473). config.yaml's `daemons.enabled` already defaults
# to true, so leaving the env var unset lets preferences.yaml be the real source of
# truth in prod. Only set CHAD_DAEMONS_ENABLED explicitly (e.g. in this process's
# environment before launching the supervisor) if you need a one-off override that
# beats the config file.

# --- Single-instance guard ---
# Only one supervisor may run per session. Two supervisors would each relaunch a
# Brain, and since server.py frees port 8767 on startup they'd ping-pong-kill each
# other into a restart loop. A named mutex (auto-released when the process dies)
# guarantees exactly one.
$script:Mutex = New-Object System.Threading.Mutex($false, "ChadBrainSupervisor")
if (-not $script:Mutex.WaitOne(0)) {
    Write-Log "Another supervisor is already running - exiting this instance."
    exit 0
}

# Clear any stale stop flag from a previous run
if (Test-Path $StopFlag) { Remove-Item $StopFlag -Force }

# --- Crash-loop backoff state ---
$MaxRestarts    = 5
$WindowSeconds  = 60
$BackoffSeconds = 30
$restartTimes   = New-Object System.Collections.Generic.Queue[datetime]

Write-Log "Chad production supervisor starting. Repo=$RepoRoot Port=$env:PORT"

while ($true) {
    if (Test-Path $StopFlag) {
        Write-Log "Stop flag detected - supervisor exiting."
        Remove-Item $StopFlag -Force
        break
    }

    $startedAt = Get-Date
    Write-Log "Launching Brain: $Python $Server"

    # Redirect stdout/stderr to FILES at the OS level (not a PowerShell pipe).
    # Piping the Brain's verbose startup logging through Tee-Object/| deadlocks:
    # Python block-buffers stdout into the pipe, the pipe fills mid-startup, and
    # Python blocks on write before it ever reaches uvicorn.serve(). File redirects
    # never block, so the Brain boots normally. (stdout/stderr must be different files.)
    $proc = Start-Process -FilePath $Python -ArgumentList @($Server) -NoNewWindow -PassThru `
        -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile
    $proc.WaitForExit()
    $exitCode = $proc.ExitCode

    $ranFor = (New-TimeSpan -Start $startedAt -End (Get-Date)).TotalSeconds
    Write-Log ("Brain exited (code={0}) after {1:N0}s." -f $exitCode, $ranFor)

    if (Test-Path $StopFlag) {
        Write-Log "Stop flag detected after exit - supervisor exiting."
        Remove-Item $StopFlag -Force
        break
    }

    # --- Crash-loop detection ---
    $now = Get-Date
    $restartTimes.Enqueue($now)
    while ($restartTimes.Count -gt 0 -and ($now - $restartTimes.Peek()).TotalSeconds -gt $WindowSeconds) {
        [void]$restartTimes.Dequeue()
    }
    if ($restartTimes.Count -gt $MaxRestarts) {
        Write-Log "Crash loop: $($restartTimes.Count) restarts in $WindowSeconds s. Backing off $BackoffSeconds s."
        Start-Sleep -Seconds $BackoffSeconds
        $restartTimes.Clear()
    } else {
        Start-Sleep -Seconds 2
    }
}

Write-Log "Supervisor stopped."
