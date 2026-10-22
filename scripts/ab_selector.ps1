# A/B: hybrid (RAG shortlist -> 9B) vs full-catalog selection on tool_selection.
# Measures pass^k + wall time per arm. ASCII only (PS 5.1). Run from repo root.
$ErrorActionPreference = "Continue"
$env:PYTHONPATH = "."
$env:CHAD_TESTING = "true"
$py = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
$repeat = if ($args.Count -ge 1) { $args[0] } else { 3 }

function Run-Arm($label, $hybrid) {
    $env:CHAD_HYBRID_SELECTION = $hybrid
    Write-Host "`n===== ARM: $label (CHAD_HYBRID_SELECTION=$hybrid, repeat=$repeat) ====="
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $out = & $py -m evals.runner --suite tool_selection --repeat $repeat 2>$null
    $sw.Stop()
    $out | Select-String -Pattern "Result\s+:", "Repeat\s+:", "FLAKY", "pass\^" | ForEach-Object { Write-Host ("  " + $_.Line.Trim()) }
    Write-Host ("  WALL TIME: {0:N1}s" -f $sw.Elapsed.TotalSeconds)
}

Run-Arm "FULL CATALOG (baseline)" "0"
Run-Arm "HYBRID (shortlist -> 9B)" "1"
Write-Host "`nDone."
