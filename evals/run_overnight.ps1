# run_overnight.ps1 -- unattended harness eval loop (ASCII-only for PS 5.1).
# Loops the FULL dataset (dry-run lane: tool selection + prompt assembly for every prompt,
# judged with qwen3.5-9b) for ~8 hours, and
# regenerates runs/scoreboard.md after every pass so you can read clustered findings on return.
#
# Robust: no server (won't die on a server crash/timeout), per-case errors tolerated, a failing
# pass never stops the loop. Uses ONLY small loaded models (never qwen3.6-35b). Run from worktree:
#   powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_overnight.ps1

$ErrorActionPreference = "Continue"
$root = if ($env:CHAD_MAIN_ROOT) { $env:CHAD_MAIN_ROOT } else { Split-Path $PSScriptRoot -Parent }
$py = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONPATH = $root
$log  = Join-Path $root "evals\runs\overnight.log"

$judgeModel = "qwen/qwen3.5-9b"
$endTime = (Get-Date).AddHours(8)
$pass = 0

Add-Content -Path $log -Value ("=== overnight eval loop started " + (Get-Date -Format o) + " until " + $endTime.ToString('o') + " ===")

while ((Get-Date) -lt $endTime) {
    $model = $judgeModel
    Add-Content -Path $log -Value ("--- pass " + $pass + " judge=" + $model + " " + (Get-Date -Format HH:mm:ss) + " ---")
    try {
        & $py -m evals.runner --all --judge --judge-model $model --repeat 2 *>> $log
    } catch {
        Add-Content -Path $log -Value ("  runner error (continuing): " + $_)
    }
    try {
        & $py -m evals.report *>> $log
    } catch {
        Add-Content -Path $log -Value ("  report error (continuing): " + $_)
    }
    $pass++
}

Add-Content -Path $log -Value ("=== overnight eval loop finished " + (Get-Date -Format o) + " after " + $pass + " passes ===")
