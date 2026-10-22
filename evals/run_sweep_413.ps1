# run_sweep_413.ps1 -- issue #413 pre-launch eval sweep (ASCII-only for PS 5.1).
#
# Runbook: evals/docs/runbooks/SWEEP_413.md
#
# Broadest unattended pass we run: free lanes + judged dry-run + LIVE + WORKFLOW, cycling for
# several hours, INCLUDING the new #413 datasets (untested MCPs, deep research stress, agent-lane
# without planner). Connect-only against whichever Brain is already running -- NEVER starts,
# restarts, or kills a server. If the target is down, that pass's live/workflow lanes are
# skipped (logged), the loop keeps going on the free/judge lanes, and it retries next pass.
#
# Usage (from repo root or worktree root):
#   powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_sweep_413.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_sweep_413.ps1 -Hours 10 -JudgeModel "google/gemma-4-26b-a4b-qat" -ServerUrl "ws://localhost:8767/ws"
#
# BEFORE running:
#   1. Confirm the judge model id actually loaded in LM Studio: (irm http://localhost:1234/v1/models).data.id
#      Must be a SMALL model (qwen/qwen3.5-9b or a gemma-*-qat variant) -- NEVER qwen/qwen3.6-35b-a3b.
#   2. Confirm the target Brain is up: irm http://localhost:8767/health   (prod, connect-only)
#      or                              irm http://localhost:8788/health   (dev)
#   3. This will create REAL content via untested MCPs (GitHub issues, Notion pages, Todoist
#      tasks, Presenton decks) per evals/datasets/mcp_untested_live.jsonl -- all prefixed
#      "Chad Eval Test" for easy bulk cleanup afterward. Read that file's header before running
#      if you want to trim which MCPs get exercised.
#
# While it runs: check evals/runs/sweep_heartbeat.json roughly hourly (or run
# evals/check_sweep_heartbeat.ps1) to confirm it hasn't silently died.

param(
    [double]$Hours = 9,
    [string]$JudgeModel = "google/gemma-4-26b-a4b-qat",
    [string]$ServerUrl = "ws://localhost:8767/ws",
    [string]$HealthUrl = "http://localhost:8767/health"
)

$ErrorActionPreference = "Continue"
$root = if ($env:CHAD_MAIN_ROOT) { $env:CHAD_MAIN_ROOT } else { Split-Path $PSScriptRoot -Parent }
$py = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONPATH   = $root
$env:CHAD_SERVER_URL = $ServerUrl
$log       = Join-Path $root "evals\runs\sweep_413.log"
$heartbeat = Join-Path $root "evals\runs\sweep_heartbeat.json"

function Log($msg) { Add-Content -Path $log -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "  " + $msg) }

function Write-Heartbeat($pass, $lane, $status) {
    $obj = @{
        pass      = $pass
        lane      = $lane
        status    = $status
        timestamp = (Get-Date -Format o)
        end_time  = $endTime.ToString('o')
    }
    ($obj | ConvertTo-Json -Compress) | Set-Content -Path $heartbeat -Encoding utf8
}

function Server-Up {
    try { return (Invoke-WebRequest $HealthUrl -TimeoutSec 5 -UseBasicParsing).StatusCode -eq 200 }
    catch { return $false }
}

function Run-Lane($pass, $laneName, $argList) {
    Write-Heartbeat $pass $laneName "running"
    Log "pass $pass -- $laneName -- START -- args: $($argList -join ' ')"
    try {
        & $py -m evals.runner @argList *>> $log
        Log "pass $pass -- $laneName -- DONE (exit $LASTEXITCODE)"
    } catch {
        Log "pass $pass -- $laneName -- ERROR: $_"
    }
    Write-Heartbeat $pass $laneName "done"
}

$endTime = (Get-Date).AddHours($Hours)
$pass = 0
Log "=== #413 sweep started, judge=$JudgeModel server=$ServerUrl until $($endTime.ToString('o')) ==="
Write-Heartbeat 0 "startup" "starting"

# Live-lane suites of interest per #413: existing coverage + the six new gap-filling datasets
# (mcp_untested_live, research_deep_live, agent_lane_complex_live, codebase_audit_live,
# website_from_data_live, document_generation_live). browser_golden needs browser_agent enabled
# (disabled_subagents in config) -- included but tolerated if it no-ops.
$liveSuites = @(
    "smoke", "live_check", "tool_args", "safety",
    "research_deep_live", "agent_lane_complex_live", "mcp_untested_live",
    "codebase_audit_live", "website_from_data_live", "document_generation_live"
)
$workflowSuites = @("workflows")

while ((Get-Date) -lt $endTime) {
    Log "--- pass $pass ---"

    # 1. FREE lanes -- always run, zero model tokens, cheap regression signal every pass.
    Run-Lane $pass "prompt-audit" @("--prompt-audit", "--judge", "--judge-model", $JudgeModel)
    Run-Lane $pass "tool-contract" @("--tool-contract")
    Run-Lane $pass "replay" @("--replay")
    Run-Lane $pass "tool_selection-repeat5" @("--suite", "tool_selection", "--repeat", "5")

    # 2. Judged dry-run sweep across EVERY dataset (every tool type, per #413 task 2).
    Run-Lane $pass "all-dry-judged" @("--all", "--judge", "--judge-model", $JudgeModel, "--repeat", "2")

    # 3. LIVE lane -- connect-only, never starts/restarts the server.
    if (Server-Up) {
        foreach ($suite in $liveSuites) {
            if (-not (Server-Up)) { Log "server went down mid-pass -- skipping remaining live suites"; break }
            Run-Lane $pass "live:$suite" @("--suite", $suite, "--live", "--judge", "--judge-model", $JudgeModel)
        }
    } else {
        Log "pass $pass -- SERVER DOWN ($HealthUrl) -- skipping ALL live/workflow lanes this pass (never auto-starting it)"
        Write-Heartbeat $pass "live" "skipped-server-down"
    }

    # 4. WORKFLOW lane -- with planner.enabled=false this also proves the #413 special-focus
    #    concern: a "planning"-category case should still complete (falls back to agent lane
    #    in round 1) rather than stalling waiting for an approval pause that never comes.
    if (Server-Up) {
        foreach ($suite in $workflowSuites) {
            Run-Lane $pass "workflow:$suite" @("--suite", $suite, "--workflow", "--judge", "--judge-model", $JudgeModel)
        }
    } else {
        Log "pass $pass -- SERVER DOWN -- skipping workflow lane this pass"
    }

    # 5. Regenerate the trend scoreboard + triage clustering so a mid-run check is useful, not
    #    just raw logs.
    try { & $py -m evals.report *>> $log } catch { Log "report error: $_" }

    Write-Heartbeat $pass "pass-complete" "done"
    Log "--- pass $pass complete ---"
    $pass++
}

Write-Heartbeat $pass "finished" "done"
Log "=== #413 sweep finished after $pass passes ==="
