# post_sweep_status.ps1 -- posts a live status snapshot of the #413 sweep to a GitHub issue,
# so progress is checkable from a phone (GitHub app/web) without RDP-ing into the PC.
#
# Reads evals/runs/sweep_heartbeat.json + evals/runs/scoreboard.md (both written by
# run_sweep_413.ps1 / evals.report while the sweep loops) and overwrites the BODY of the target
# issue (default #474, the dedicated live-status issue) via `gh issue edit`. Never posts a new
# comment each run -- editing the body keeps the issue a single always-current snapshot instead
# of a scrolling log (evals/runs/sweep_413.log is the log; issue #413 is the original task).
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File evals/post_sweep_status.ps1
#        powershell -NoProfile -ExecutionPolicy Bypass -File evals/post_sweep_status.ps1 -IssueNumber 474

param(
    [int]$IssueNumber = 474,
    [string]$Repo = "MatthewLoyed/AIAssistant",
    [int]$StaleMinutes = 75
)

$root = if ($env:CHAD_MAIN_ROOT) { $env:CHAD_MAIN_ROOT } else { Split-Path $PSScriptRoot -Parent }
$heartbeat = Join-Path $root "evals\runs\sweep_heartbeat.json"
$scoreboard = Join-Path $root "evals\runs\scoreboard.md"
$bodyFile  = Join-Path $root "evals\runs\sweep_status_body.md"

$lines = @()
$lines += "# Eval sweep #413 -- live status"
$lines += ""
$lines += "_Auto-updated by a scheduled task (``evals/post_sweep_status.ps1``) roughly hourly. Last refresh: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss K')._"
$lines += ""

if (-not (Test-Path $heartbeat)) {
    $lines += "**Heartbeat: MISSING** -- no heartbeat file found at ``evals/runs/sweep_heartbeat.json``. Sweep may not have started, or the path changed."
} else {
    $hb = Get-Content $heartbeat -Raw | ConvertFrom-Json
    $age = (Get-Date) - [datetime]$hb.timestamp
    $endTime = [datetime]$hb.end_time
    $ageMin = [math]::Round($age.TotalMinutes, 1)

    if ($hb.lane -eq "finished") {
        $lines += "**Status: FINISHED** -- sweep completed its scheduled window (ended $($hb.end_time))."
    } elseif ($ageMin -gt $StaleMinutes) {
        $lines += "**Status: STALE** -- heartbeat hasn't moved in $ageMin min (threshold $StaleMinutes). Likely a hung judge model or a dead Brain process (port 8767) -- check ``evals/runs/sweep_413.log``."
    } else {
        $lines += "**Status: OK** -- alive, heartbeat is fresh ($ageMin min old)."
    }
    $lines += ""
    $lines += "| Field | Value |"
    $lines += "|---|---|"
    $lines += "| Pass | $($hb.pass) |"
    $lines += "| Lane | ``$($hb.lane)`` |"
    $lines += "| Lane status | $($hb.status) |"
    $lines += "| Last heartbeat | $($hb.timestamp) |"
    $lines += "| Scheduled end | $($hb.end_time) |"
}

$lines += ""
$lines += "## Scoreboard snapshot"
$lines += ""

if (-not (Test-Path $scoreboard)) {
    $lines += "_No scoreboard yet -- the first pass hasn't finished a full report cycle. Check back next hour._"
} else {
    $sb = Get-Content $scoreboard -Raw
    # Header block: everything up to (not including) the first "### " cluster heading.
    $headerEnd = $sb.IndexOf("### ")
    if ($headerEnd -lt 0) { $headerEnd = $sb.Length }
    $header = $sb.Substring(0, $headerEnd).TrimEnd()
    $lines += $header
    $lines += ""

    # Top 5 failure clusters only (full detail lives in the file on disk).
    $clusterMatches = [regex]::Matches($sb, "(?ms)^### .*?(?=^### |\z)")
    $top = $clusterMatches | Select-Object -First 5
    if ($top.Count -gt 0) {
        foreach ($m in $top) { $lines += $m.Value.TrimEnd(); $lines += "" }
        if ($clusterMatches.Count -gt 5) {
            $lines += "_...and $($clusterMatches.Count - 5) more cluster(s) -- see ``evals/runs/scoreboard.md`` on the PC for the full list._"
        }
    }
}

$lines += ""
$lines += "---"
$lines += "Tracking issue: #413 | Full detail on the PC: ``evals/runs/scoreboard.md``, ``evals/runs/triage.md``, ``evals/runs/sweep_413.log``"

($lines -join "`n") | Set-Content -Path $bodyFile -Encoding ascii

& gh issue edit $IssueNumber --repo $Repo --body-file $bodyFile
if ($LASTEXITCODE -ne 0) {
    Write-Output "ERROR: gh issue edit failed (exit $LASTEXITCODE)"
    exit 1
}
Write-Output "OK: posted status to issue #$IssueNumber"
