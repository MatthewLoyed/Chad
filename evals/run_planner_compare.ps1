# Quick head-to-head: Gemma 31B vs Qwen3 14B on planner quality.
# Requires: Brain server on :8767, both models loaded in LM Studio, qwen3.5-9b loaded for judge.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_planner_compare.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py   = Join-Path $root ".venv\Scripts\python.exe"

$env:PYTHONPATH = $root

Write-Host "Loaded LM Studio models:"
try {
    (Invoke-RestMethod "http://localhost:1234/v1/models").data.id | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Host "  (LM Studio not reachable — load models first)"
}

& $py -m evals.runner `
    --suite planner `
    --live `
    --judge `
    --judge-model "qwen/qwen3.5-9b" `
    --models "google/gemma-4-31b-qat,qwen/qwen3-14b"

Write-Host ""
Write-Host "Report: evals/runs/compare_latest.md"
