# run_chad_on_chad.ps1 -- one-command entry point for the #358/#596 self-eval driver.
#
# Runs evals/chad_on_chad_driver.py's run_cycle(case) end to end: BEFORE turn -> Chad diagnoses
# -> provisions a SHA-pinned clone -> dispatches Chad Code to fix it -> reruns AFTER against the
# clone -> Chad judges -> report saved to evals/runs/chad_on_chad/. No wall-clock cap on the turn
# stages (a real local-model turn can be 20+ minutes) -- this can run a long time. DEV-ONLY, see
# evals/SELF_EVAL_DO_NOT_SHIP.md.
#
# BEFORE running:
#   1. Docker Desktop running:      docker info
#   2. LM Studio reachable + the coder model downloaded: (irm http://localhost:1234/v1/models).data.id
#
# Usage (from repo root or worktree root):
#   powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_chad_on_chad.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_chad_on_chad.ps1 -Case "golfport-mobile-app" -KeepClone

param(
    [string]$Case = "annual-report-advisor-initials",
    [switch]$KeepClone
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { throw "Missing project Python: $py" }
Set-Location -LiteralPath $root
$env:PYTHONPATH = $root

Write-Host "[run_chad_on_chad] case='$Case' keep_clone=$($KeepClone.IsPresent)"

$env:CHAD_SELF_EVAL_CASE = $Case
$env:CHAD_SELF_EVAL_KEEP_CLONE = if ($KeepClone) { "1" } else { "0" }
& $py -c @"
import asyncio, os
from evals.chad_on_chad_driver import run_cycle
asyncio.run(run_cycle(os.environ['CHAD_SELF_EVAL_CASE'], keep_clone=os.environ['CHAD_SELF_EVAL_KEEP_CLONE'] == '1'))
"@
exit $LASTEXITCODE
