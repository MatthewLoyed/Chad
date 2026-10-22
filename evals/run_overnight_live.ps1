# run_overnight_live.ps1 -- unattended LIVE + WORKFLOW eval loop (ASCII-only for PS 5.1).
#
# Unlike run_overnight.ps1 (dry-run, no server), this exercises the REAL agent end-to-end:
#   - --live --all        : every prompt as a real server turn (executed tools, grounded response)
#   - --workflow workflows: full plan -> auto-approve -> execute loop
# for ~8 hours, judging with qwen3.5-9b, regenerating runs/scoreboard.md each pass.
#
# Manages the Brain server itself: starts one on the pinned brain model below, waits for
# bootstrap, and RESTARTS it if it dies mid-run. Uses ONLY loaded small models (never qwen3.6-35b).
#   powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_overnight_live.ps1

$ErrorActionPreference = "Continue"
$root = if ($env:CHAD_MAIN_ROOT) { $env:CHAD_MAIN_ROOT } else { Split-Path $PSScriptRoot -Parent }
$brainModel = "google/gemma-4-31b-qat"
$judgeModel = "qwen/qwen3.5-9b"
$py = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONPATH = $root
$log     = Join-Path $root "evals\runs\overnight_live.log"
$srvlog  = Join-Path $root "evals\runs\_live_server.log"
$health  = "http://localhost:8767/health"

function Log($msg) { Add-Content -Path $log -Value ((Get-Date -Format HH:mm:ss) + "  " + $msg) }

function Server-Up {
    try { return (Invoke-WebRequest $health -TimeoutSec 4 -UseBasicParsing).StatusCode -eq 200 }
    catch { return $false }
}

function Ensure-Server {
    if (Server-Up) { return (Confirm-BrainModel) }
    Log "server down -> starting fresh Brain server (brain=$brainModel)"
    $env:CHAD_DAEMONS_ENABLED = "false"
    Start-Process $py -ArgumentList @("$root\src\remote\server.py") -WindowStyle Hidden -RedirectStandardOutput $srvlog -RedirectStandardError ($srvlog + ".err")
    # Wait up to ~3 min for health + model bootstrap.
    for ($i = 0; $i -lt 36; $i++) {
        Start-Sleep -Seconds 5
        if (Server-Up) {
            Log "server health OK after ~$($i*5)s; giving bootstrap 40s"
            Start-Sleep -Seconds 40
            return (Confirm-BrainModel)
        }
    }
    Log "server did NOT come up in 3 min -- skipping this pass"
    return $false
}

function Confirm-BrainModel {
    $swap = @"
import asyncio
from evals.compare import hot_swap_model, get_active_model_from_config
async def main():
    await hot_swap_model('$brainModel')
    print('active=' + str(get_active_model_from_config()))
asyncio.run(main())
"@
    try {
        $out = & $py -c $swap 2>&1 | Out-String
        Log "brain swap: $($out.Trim())"
    } catch {
        Log "brain swap failed: $_"
        return $false
    }
    Start-Sleep -Seconds 5
    $hits = Select-String -Path $srvlog, ($srvlog + ".err") -Pattern "Local active: False|gemini|cloud" -ErrorAction SilentlyContinue
    if ($hits) {
        Log "PREFLIGHT FAIL: cloud drift detected in server log"
        return $false
    }
    $modelHits = Select-String -Path $srvlog, ($srvlog + ".err") -Pattern $brainModel.Replace('/', '/') -ErrorAction SilentlyContinue
    if (-not $modelHits) {
        Log "PREFLIGHT WARN: no log line for $brainModel yet (continuing)"
    }
    return $true
}
function Preflight-Clear {
    # Full dependency gate (evals/preflight.py): forces a real Google OAuth refresh and
    # checks the credential outlives this 8-hour window, asserts the search backend returns
    # hits, confirms the judge model is already served, etc. Exit 2 = blocked.
    #
    # This is the difference between "we woke up to 8 hours of results" and "we woke up to 8
    # hours of cases that all failed because a token died at 2am" (issue #476, which
    # contaminated a #413 sweep exactly this way).
    & $py -m evals.preflight --live --judge-model $judgeModel *>> $log
    if ($LASTEXITCODE -eq 2) {
        Log "PREFLIGHT BLOCKED -- aborting this pass instead of generating junk results. See the checks above in this log."
        return $false
    }
    if ($LASTEXITCODE -ne 0) {
        Log "PREFLIGHT errored (exit $LASTEXITCODE) -- aborting this pass"
        return $false
    }
    Log "preflight clear"
    return $true
}

$endTime = (Get-Date).AddHours(8)
$pass = 0
Log "=== LIVE overnight loop started, until $($endTime.ToString('o')) ==="

while ((Get-Date) -lt $endTime) {
    $model = $judgeModel
    Log "--- pass $pass brain=$brainModel judge=$model ---"
    if (Ensure-Server) {
        if (Preflight-Clear) {
            try { & $py -m evals.runner --live --all --judge --judge-model $model *>> $log } catch { Log "live error: $_" }
            if (Ensure-Server) {
                try { & $py -m evals.runner --workflow --suite workflows --judge --judge-model $model *>> $log } catch { Log "workflow error: $_" }
            }
        } else {
            # Don't spin: a broken dependency won't fix itself in 30 seconds, and a hot loop
            # of aborted passes buries the reason in the log.
            Log "sleeping 15 min before re-checking the environment"
            Start-Sleep -Seconds 900
        }
    }
    try { & $py -m evals.report *>> $log } catch { Log "report error: $_" }
    $pass++
}
Log "=== LIVE overnight loop finished after $pass passes ==="
# Longitudinal view of everything this loop produced.
try { & $py -m evals.runner --scoreboard *>> $log } catch { Log "scoreboard error: $_" }
