# check_sweep_heartbeat.ps1 -- one-shot liveness check for run_sweep_413.ps1.
#
# Meant to be run ~hourly while the sweep is looping (issue #413: "check in every hour to make
# sure the evals didn't completely crash / we're not wasting time"). Reads
# evals/runs/sweep_heartbeat.json (written after every lane by run_sweep_413.ps1) and reports:
#   OK      - heartbeat updated within the last StaleMinutes -> sweep is alive
#   STALE   - heartbeat exists but hasn't moved -> likely crashed/hung, tail the log
#   MISSING - heartbeat file not found -> sweep never started or was moved/deleted
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File evals/check_sweep_heartbeat.ps1
#        powershell -NoProfile -ExecutionPolicy Bypass -File evals/check_sweep_heartbeat.ps1 -StaleMinutes 75

param([int]$StaleMinutes = 75)

$root = if ($env:CHAD_MAIN_ROOT) { $env:CHAD_MAIN_ROOT } else { Split-Path $PSScriptRoot -Parent }
$heartbeat = Join-Path $root "evals\runs\sweep_heartbeat.json"
$log       = Join-Path $root "evals\runs\sweep_413.log"

if (-not (Test-Path $heartbeat)) {
    Write-Output "MISSING: no heartbeat file at $heartbeat -- sweep never started, or path changed."
    exit 2
}

$hb = Get-Content $heartbeat -Raw | ConvertFrom-Json
$age = (Get-Date) - [datetime]$hb.timestamp
$endTime = [datetime]$hb.end_time

Write-Output "pass=$($hb.pass) lane=$($hb.lane) status=$($hb.status) last_update=$($hb.timestamp) age_min=$([math]::Round($age.TotalMinutes,1)) scheduled_end=$($hb.end_time)"

if ((Get-Date) -gt $endTime -and $hb.lane -eq "finished") {
    Write-Output "OK: sweep completed its full scheduled window (ended $($hb.end_time)) as of $($hb.timestamp)."
    exit 0
}

if ($age.TotalMinutes -gt $StaleMinutes) {
    Write-Output "STALE: heartbeat hasn't moved in $([math]::Round($age.TotalMinutes,1)) min (threshold $StaleMinutes). Likely crashed or hung."
    if (Test-Path $log) {
        Write-Output "--- last 25 log lines ($log) ---"
        Get-Content $log -Tail 25
    }
    exit 1
}

Write-Output "OK: sweep alive, heartbeat is fresh."
exit 0
