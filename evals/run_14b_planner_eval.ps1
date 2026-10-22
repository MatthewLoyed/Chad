# Evaluate qwen/qwen3-14b on planner + simple agent tasks.
# Requires: Brain server on :8767, qwen3-14b + qwen3.5-9b loaded in LM Studio.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_14b_planner_eval.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py   = Join-Path $root ".venv\Scripts\python.exe"

$env:PYTHONPATH = $root

Write-Host "Loaded LM Studio models:"
try {
    (Invoke-RestMethod "http://localhost:1234/v1/models").data.id | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Host "  (LM Studio not reachable — load qwen3-14b and qwen3.5-9b first)"
    exit 1
}

Write-Host ""
Write-Host "=== Swapping Brain to qwen/qwen3-14b, running planner + smoke suites ==="

& $py -m evals.runner `
    --suite planner `
    --live `
    --judge `
    --judge-model "qwen/qwen3.5-9b" `
    --models "qwen/qwen3-14b"

Write-Host ""
Write-Host "=== Simple agent task (time query) ==="

& $py -m evals.runner `
    --suite smoke `
    --live `
    --judge `
    --judge-model "qwen/qwen3.5-9b" `
    --models "qwen/qwen3-14b"

Write-Host ""
Write-Host "Reports:"
Write-Host "  evals/runs/compare_latest.md  (planner matrix)"
Write-Host "  evals/runs/latest.md          (smoke run)"
