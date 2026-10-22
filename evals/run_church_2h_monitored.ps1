# run_church_2h_monitored.ps1 -- 2h durable live evals with hard phase timeouts + stall kills.
# ASCII-only. Skips fault_recovery. Dry-run = assertions only (no Gemma VOID storm).
# Live/workflow judged with Gemma-4-26b. Continues after phase fail/timeout.

$ErrorActionPreference = "Continue"
$main = if ($env:CHAD_MAIN_ROOT) { $env:CHAD_MAIN_ROOT } else { Split-Path $PSScriptRoot -Parent }
$wt = if ($env:CHAD_WORKTREE_ROOT) { $env:CHAD_WORKTREE_ROOT } else { Split-Path $PSScriptRoot -Parent }
$py   = "$main\.venv\Scripts\python.exe"
$judge = "google/gemma-4-26b-a4b-qat"

$outRoot = Join-Path $wt "evals\runs\church_2h"
$phases  = Join-Path $outRoot "phases"
$snap    = Join-Path $outRoot "snapshots"
$index   = Join-Path $outRoot "INDEX.md"
$master  = Join-Path $outRoot "master.log"
$deadline = (Get-Date).AddHours(2)

New-Item -ItemType Directory -Force -Path $phases, $snap | Out-Null
Set-Location $wt

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $master -Value $line -Encoding utf8
    Write-Host $line
}

function Append-Index([string]$s) { Add-Content -Path $index -Value $s -Encoding utf8 }

function Health-Ok {
    try {
        return ((Invoke-WebRequest "http://localhost:8788/health" -TimeoutSec 4 -UseBasicParsing).StatusCode -eq 200)
    } catch { return $false }
}

function Snapshot-Latest([string]$name) {
    $dest = Join-Path $snap $name
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    foreach ($f in @("latest.md","latest.json")) {
        $src = Join-Path $wt ("evals\runs\" + $f)
        if (Test-Path $src) { Copy-Item $src (Join-Path $dest $f) -Force }
    }
}

function Kill-Tree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    try {
        & taskkill /PID $ProcessId /T /F 2>$null | Out-Null
    } catch {}
}

function Kill-EvalPy {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -match 'evals\.runner' -or
            $_.CommandLine -match 'evals\.preflight' -or
            $_.CommandLine -match 'evals\.fault_recovery' -or
            $_.CommandLine -match 'evals\.report'
        )
    } | ForEach-Object {
        Log ("kill eval pid={0}" -f $_.ProcessId)
        Kill-Tree $_.ProcessId
    }
}

function Set-EvalEnv {
    $env:PYTHONPATH = $wt
    $env:CHAD_MAIN_ROOT = $main
    $env:CHAD_SERVER_URL = "ws://localhost:8788/ws"
    $env:CHAD_DAEMONS_ENABLED = "false"
    $env:LANGSMITH_TRACING = "false"
    $env:LANGCHAIN_TRACING_V2 = "false"
    $env:CHAD_SECRETS_PATH = (Join-Path $main "config\secrets.yaml")
    $env:EVAL_TOOL_RESULT_CHARS = "16000"
    $env:EVAL_TOOL_RESULTS_TOTAL_CHARS = "64000"
    $env:PYTHONUNBUFFERED = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $mainEnv = Join-Path $main ".env"
    if (Test-Path $mainEnv) {
        Get-Content $mainEnv | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith("#") -or ($line.IndexOf("=") -lt 1)) { return }
            $k, $v = $line.Split("=", 2)
            $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
            if ($k -in @("CHAD_REMOTE_TOKEN","CHAD_SERVER_TOKEN")) {
                Set-Item -Path ("Env:{0}" -f $k) -Value $v
            }
        }
    }
}

# Run a python -m module with timeout + stall kill. Args after module name.
function Run-PyModule([string]$name, [int]$timeoutMin, [string[]]$pyArgs) {
    if ((Get-Date) -ge $deadline) {
        Log ("SKIP {0}: past 2h deadline" -f $name)
        Append-Index ""; Append-Index ("## {0}" -f $name); Append-Index "- result: SKIP (deadline)"
        return 124
    }
    $phaseLog = Join-Path $phases ("{0}.log" -f $name)
    "" | Set-Content -Path $phaseLog -Encoding utf8
    Log ("=== PHASE START: {0} timeout={1}m args={2} ===" -f $name, $timeoutMin, ($pyArgs -join " "))
    Append-Index ""
    Append-Index ("## {0}" -f $name)
    Append-Index ("- started: {0}" -f (Get-Date -Format o))
    Append-Index ("- timeout_min: {0}" -f $timeoutMin)

    if (-not (Health-Ok)) {
        Log ("SKIP {0}: brain down" -f $name)
        Append-Index "- result: SKIP (brain down)"
        return 2
    }

    Set-EvalEnv
    $argLine = @("-u", "-m") + $pyArgs
    $proc = Start-Process -FilePath $py -ArgumentList $argLine -WorkingDirectory $wt `
        -RedirectStandardOutput $phaseLog -RedirectStandardError ($phaseLog + ".err") `
        -PassThru -WindowStyle Hidden

    $phaseEnd = (Get-Date).AddMinutes($timeoutMin)
    $lastSize = -1
    $stallSince = Get-Date
    $status = "finished"
    $exit = -1

    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 20
        if ((Get-Date) -ge $deadline) {
            Log ("DEADLINE kill {0}" -f $name)
            Kill-Tree $proc.Id; Kill-EvalPy
            $status = "killed_deadline"; $exit = 124; break
        }
        if ((Get-Date) -ge $phaseEnd) {
            Log ("TIMEOUT kill {0} after {1}m" -f $name, $timeoutMin)
            Kill-Tree $proc.Id; Kill-EvalPy
            $status = "killed_timeout"; $exit = 124; break
        }
        $sz = 0
        if (Test-Path $phaseLog) { $sz = (Get-Item $phaseLog).Length }
        $errLog = $phaseLog + ".err"
        if (Test-Path $errLog) { $sz += (Get-Item $errLog).Length }
        if ($sz -ne $lastSize) {
            $lastSize = $sz
            $stallSince = Get-Date
        } elseif (((Get-Date) - $stallSince).TotalMinutes -ge 10) {
            $runningMin = ((Get-Date) - $proc.StartTime).TotalMinutes
            if ($runningMin -ge 3) {
                Log ("STALL kill {0}: no log growth for 10m (size={1})" -f $name, $sz)
                Kill-Tree $proc.Id; Kill-EvalPy
                $status = "killed_stall"; $exit = 125; break
            }
        }
    }

    if ($exit -lt 0) {
        try { $proc.Refresh(); $exit = $proc.ExitCode } catch { $exit = 1 }
    }
    Snapshot-Latest $name
    Log ("=== PHASE END: {0} status={1} exit={2} ===" -f $name, $status, $exit)
    Append-Index ("- status: {0}" -f $status)
    Append-Index ("- exit_code: {0}" -f $exit)
    Append-Index ("- ended: {0}" -f (Get-Date -Format o))
    Append-Index ("- log: phases/{0}.log" -f $name)
    return $exit
}

@(
    "# Church 2h monitored eval"
    ""
    ("- started: {0}" -f (Get-Date -Format o))
    ("- deadline: {0}" -f $deadline.ToString("o"))
    ("- worktree: {0}" -f $wt)
    "- brain: Dev worktree :8788"
    ("- judge (live/workflow): {0}" -f $judge)
    "- skip: fault_recovery; dry-run --judge (Gemma VOID storm)"
    "- monitor: 10m stall kill + per-phase hard timeout"
    ""
) | Set-Content -Path $index -Encoding utf8

Log ("=== church 2h started until {0} ===" -f $deadline.ToString("o"))

# Free lanes already durable from prior attempt (snapshots/01_*, 02_*). Resume at live.
# 0) Preflight must clear auth/google/judge
[void](Run-PyModule "00_preflight" 5 @("evals.preflight","--suite","church_safe_live","--live","--judge-model",$judge))

# Live judged (Gemma) -- the valuable part of this window
[void](Run-PyModule "03_live_check" 25 @("evals.runner","--suite","live_check","--live","--judge","--judge-model",$judge,"--skip-preflight"))
[void](Run-PyModule "04_church_safe_live" 50 @("evals.runner","--suite","church_safe_live","--live","--judge","--judge-model",$judge,"--skip-preflight"))
[void](Run-PyModule "05_browser_golden" 50 @("evals.runner","--suite","browser_golden","--live","--judge","--judge-model",$judge,"--skip-preflight"))

# Workflows (first 6 cases to fit remaining time)
[void](Run-PyModule "06_workflows_limit6" 40 @("evals.runner","--suite","workflows","--workflow","--judge","--judge-model",$judge,"--limit","6","--skip-preflight"))

# 4) Rollup
[void](Run-PyModule "07_report" 5 @("evals.report"))
[void](Run-PyModule "08_scoreboard" 5 @("evals.runner","--scoreboard"))

$summary = Join-Path $outRoot "SUMMARY.md"
$parts = New-Object System.Collections.Generic.List[string]
[void]$parts.Add("# Church 2h SUMMARY")
[void]$parts.Add("")
[void]$parts.Add(("Generated: {0}" -f (Get-Date -Format o)))
[void]$parts.Add("")
if (Test-Path $index) { [void]$parts.Add((Get-Content $index -Raw)) }
$latest = Join-Path $wt "evals\runs\latest.md"
if (Test-Path $latest) {
    [void]$parts.Add("")
    [void]$parts.Add("## Last latest.md")
    [void]$parts.Add((Get-Content $latest -Raw))
}
Set-Content -Path $summary -Value ($parts -join "`n") -Encoding utf8

Append-Index ""
Append-Index "---"
Append-Index ("- finished: {0}" -f (Get-Date -Format o))
Append-Index "- summary: SUMMARY.md"
Log "=== church 2h finished ==="

