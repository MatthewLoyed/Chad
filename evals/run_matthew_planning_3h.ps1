# run_matthew_planning_3h.ps1 — 3-Hour Monitored Planning -> Execution Suite
# Targeted against Dev Brain (:8788). Does NOT touch prod (:8767).
# ASCII-only output, with stall watchdog, auto-triage, and transcript preservation.

param(
    [double]$Hours = 3.0,
    [string]$Judge = "google/gemma-4-26b-a4b-qat",
    [string]$Port = "8788"
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path ".").Path
$Py   = Join-Path $Root ".venv\Scripts\python.exe"
$RunsDir = Join-Path $Root "evals\runs"
$LogDir  = Join-Path $RunsDir "matthew_planning_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
$MasterLog = Join-Path $LogDir "master.log"
$Deadline  = (Get-Date).AddHours($Hours)

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $MasterLog -Value $line -Encoding utf8
    Write-Host $line
}

function Health-Ok {
    for ($i = 0; $i -lt 3; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri "http://localhost:${Port}/health" -TimeoutSec 15 -UseBasicParsing
            if ($resp.StatusCode -eq 200) { return $true }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Set-EvalEnv {
    $env:PYTHONPATH = $Root
    $env:CHAD_SERVER_URL = "ws://localhost:${Port}/ws"
    $env:CHAD_DAEMONS_ENABLED = "false"
    $env:LANGSMITH_TRACING = "false"
    $env:LANGCHAIN_TRACING_V2 = "false"
    $env:PYTHONUNBUFFERED = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:EVAL_TOOL_RESULT_CHARS = "0"
    $env:EVAL_TOOL_RESULTS_TOTAL_CHARS = "0"
    $env:EVAL_ROUND_IDLE_CHECK_S = "480"
    $env:EVAL_ROUND_MAX_IDLE_CHECKS = "3"

    # Pull auth tokens from .env
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith("#") -or ($line.IndexOf("=") -lt 1)) { return }
            $k, $v = $line.Split("=", 2)
            $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
            if ($k -in @("CHAD_REMOTE_TOKEN", "CHAD_SERVER_TOKEN")) {
                Set-Item -Path ("Env:{0}" -f $k) -Value $v
            }
        }
    }
}

Log "=== MATTHEW PLANNING -> EXECUTION 3-HOUR EVAL RUNNER ==="
Log "Target Server : http://localhost:${Port} (Dev Brain)"
Log "Deadline      : $($Deadline.ToString('o')) ($Hours hours)"
Log "Judge Model   : $Judge"
Log "Log Output    : $LogDir"

if (-not (Health-Ok)) {
    Log "[ERROR] Dev Brain is not running or not healthy on port ${Port}!"
    Log "Please start Dev Brain first with: .\scripts\prod\launch_dev.ps1 -DisableDaemons -NoNgrok"
    exit 2
}

Set-EvalEnv

$runLog = Join-Path $LogDir "runner.log"
$runErr = Join-Path $LogDir "runner.err"

Log "Starting sequential workflow eval for suite 'matthew_planning_live'..."

$argsList = @(
    "-u", "-m", "evals.runner",
    "--suite", "matthew_planning_live",
    "--workflow",
    "--judge",
    "--judge-model", $Judge,
    "--skip-preflight"
)

$proc = Start-Process -FilePath $Py -ArgumentList $argsList -WorkingDirectory $Root `
    -RedirectStandardOutput $runLog -RedirectStandardError $runErr `
    -PassThru -WindowStyle Hidden

Log "Launched runner process PID=$($proc.Id)"

$stallSince = Get-Date
$lastSize = -1

while (-not $proc.HasExited) {
    if ((Get-Date) -ge $Deadline) {
        Log "[DEADLINE] 3-Hour window exceeded. Terminating runner process PID=$($proc.Id)..."
        try { taskkill /PID $proc.Id /T /F 2>$null | Out-Null } catch {}
        break
    }

    if (-not (Health-Ok)) {
        Log "[ALERT] Dev Brain went offline mid-run! Stopping..."
        try { taskkill /PID $proc.Id /T /F 2>$null | Out-Null } catch {}
        break
    }

    $sz = 0
    if (Test-Path $runLog) { $sz = (Get-Item $runLog).Length }
    if (Test-Path $runErr) { $sz += (Get-Item $runErr).Length }

    if ($sz -ne $lastSize) {
        $lastSize = $sz
        $stallSince = Get-Date
    } else {
        $stalledMins = [math]::Round(((Get-Date) - $stallSince).TotalMinutes, 1)
        if ($stalledMins -ge 45) {
            Log "[STALL] No log activity detected for $stalledMins minutes (size=$sz). Terminating stuck turn..."
            try { taskkill /PID $proc.Id /T /F 2>$null | Out-Null } catch {}
            break
        }
    }

    Start-Sleep -Seconds 20
}

Log "Runner process finished. ExitCode=$($proc.ExitCode)"

# Run Triage clustering
Log "Running triage clustering on latest results..."
try {
    & $Py -m evals.triage
    Log "Triage clustering updated: evals/runs/triage.md"
} catch {
    Log "Triage clustering failed: $_"
}

Log "=== EVAL RUN COMPLETE ==="
Log "Summary Report : evals/runs/latest.md"
Log "Triage Report  : evals/runs/triage.md"
Log "Full Wave Log  : $runLog"
