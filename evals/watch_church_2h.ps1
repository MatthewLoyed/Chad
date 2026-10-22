# 10-minute watchdog for church_2h monitored run. ASCII-only.
# Restarts Dev Brain if down; relaunches monitored script if the orchestrator dies early.

$ErrorActionPreference = "Continue"
$wt = if ($env:CHAD_WORKTREE_ROOT) { $env:CHAD_WORKTREE_ROOT } else { Split-Path $PSScriptRoot -Parent }
$main = if ($env:CHAD_MAIN_ROOT) { $env:CHAD_MAIN_ROOT } else { Split-Path $PSScriptRoot -Parent }
$outRoot = Join-Path $wt "evals\runs\church_2h"
$master  = Join-Path $outRoot "master.log"
$watch   = Join-Path $outRoot "watchdog.log"
$deadline = (Get-Date).AddHours(2)
$script = Join-Path $wt "evals\run_church_2h_monitored.ps1"

function WLog([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $watch -Value $line -Encoding utf8
    Write-Host $line
}

function Health-Ok {
    try {
        return ((Invoke-WebRequest "http://localhost:8788/health" -TimeoutSec 4 -UseBasicParsing).StatusCode -eq 200)
    } catch { return $false }
}

function Ensure-Brain {
    if (Health-Ok) { return $true }
    WLog "brain 8788 DOWN - attempting restart"
    $py = Join-Path $main ".venv\Scripts\python.exe"
    $env:CHAD_SERVER_PORT = "8788"
    $env:CHAD_DAEMONS_ENABLED = "false"
    $env:PYTHONPATH = $wt
    $env:CHAD_MAIN_ROOT = $main
    $env:CHAD_SECRETS_PATH = (Join-Path $main "config\secrets.yaml")
    # Prefer existing start helper if present
    $start = Join-Path $wt "scripts\start_dev_brain.ps1"
    if (Test-Path $start) {
        Start-Process powershell.exe -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$start) -WorkingDirectory $wt -WindowStyle Hidden
    } else {
        Start-Process -FilePath $py -ArgumentList @("-m","server") -WorkingDirectory $wt -WindowStyle Hidden
    }
    Start-Sleep -Seconds 12
    $ok = Health-Ok
    WLog ("brain restart ok={0}" -f $ok)
    return $ok
}

function Orchestrator-Alive {
    $hit = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match 'run_church_2h_monitored\.ps1'
    }
    return [bool]$hit
}

function Runner-Alive {
    $hit = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match 'evals\.runner'
    }
    return [bool]$hit
}

function Phase-Log-Summary {
    $phases = Join-Path $outRoot "phases"
    if (-not (Test-Path $phases)) { return "no phases dir" }
    $parts = @()
    Get-ChildItem $phases -Filter "*.log" | Where-Object { $_.Name -notlike "*.err" } | ForEach-Object {
        $parts += ("{0}={1}b" -f $_.BaseName, $_.Length)
    }
    return ($parts -join ", ")
}

function Last-Master-Lines([int]$n = 5) {
    if (-not (Test-Path $master)) { return "(no master.log)" }
    return ((Get-Content $master -Tail $n) -join " | ")
}

WLog ("=== watchdog start until {0} ===" -f $deadline.ToString("o"))

$checks = 0
while ((Get-Date) -lt $deadline) {
    $checks++
    [void](Ensure-Brain)
    $orch = Orchestrator-Alive
    $run = Runner-Alive
    $summary = Phase-Log-Summary
    $tail = Last-Master-Lines 4
    WLog ("check#{0} orch={1} runner={2} | {3}" -f $checks, $orch, $run, $summary)
    WLog ("master: {0}" -f $tail)

    # If SUMMARY exists, run completed
    if (Test-Path (Join-Path $outRoot "SUMMARY.md")) {
        WLog "SUMMARY.md present - watchdog exiting"
        break
    }

    # If orchestrator died before deadline and no SUMMARY, relaunch resume script
    if (-not $orch -and -not (Test-Path (Join-Path $outRoot "SUMMARY.md"))) {
        $last = ""
        if (Test-Path $master) { $last = (Get-Content $master -Tail 1) }
        if ($last -match "ALL DONE|SUMMARY") {
            WLog "orchestrator finished normally"
            break
        }
        WLog "orchestrator dead early - relaunching monitored script"
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile","-ExecutionPolicy","Bypass","-File",$script
        ) -WorkingDirectory $wt -WindowStyle Hidden | Out-Null
        Start-Sleep -Seconds 30
    }

    # Stall hint: runner alive but phase logs unchanged for long - script handles kill;
    # here we only log so human/agent can see.
    Start-Sleep -Seconds 600
}

WLog "=== watchdog end ==="
