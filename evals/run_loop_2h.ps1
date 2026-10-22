# Parallel eval wave runner for the eval→observe→fix loop.
# Starts up to 3 suites at once against Dev Brain :8788. ASCII-only.
# Does NOT auto-fix code — agent reads wave logs and decides.

param(
    [int]$Hours = 2,
    [string]$Judge = "google/gemma-4-26b-a4b-qat"
)

$ErrorActionPreference = "Continue"
$main = if ($env:CHAD_MAIN_ROOT) { $env:CHAD_MAIN_ROOT } else { Split-Path $PSScriptRoot -Parent }
$wt = if ($env:CHAD_WORKTREE_ROOT) { $env:CHAD_WORKTREE_ROOT } else { Split-Path $PSScriptRoot -Parent }
$py   = "$main\.venv\Scripts\python.exe"
$outRoot = Join-Path $wt "evals\runs\loop_2h"
$waves = Join-Path $outRoot "waves"
$master = Join-Path $outRoot "master.log"
$deadline = (Get-Date).AddHours($Hours)

New-Item -ItemType Directory -Force -Path $waves | Out-Null

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $master -Value $line -Encoding utf8
    Write-Host $line
}

function Health-Ok {
    try {
        return ((Invoke-WebRequest "http://localhost:8788/health" -TimeoutSec 4 -UseBasicParsing).StatusCode -eq 200)
    } catch { return $false }
}

function Set-EvalEnv {
    $env:PYTHONPATH = $wt
    $env:CHAD_MAIN_ROOT = $main
    $env:CHAD_SERVER_URL = "ws://localhost:8788/ws"
    $env:CHAD_DAEMONS_ENABLED = "false"
    $env:LANGSMITH_TRACING = "false"
    $env:LANGCHAIN_TRACING_V2 = "false"
    $env:CHAD_SECRETS_PATH = (Join-Path $main "config\secrets.yaml")
    $env:PYTHONUNBUFFERED = "1"
    $env:PYTHONIOENCODING = "utf-8"
    # Prefer judge evidence over aggressive caps for list tools (gmail fix is in result text)
    $env:EVAL_TOOL_RESULT_CHARS = "0"
    $env:EVAL_TOOL_RESULTS_TOTAL_CHARS = "0"
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

# Each wave: at most ONE live/workflow job (LM Studio thrash), plus free lanes in parallel.
# Found 2026-07-26: 3 concurrent --live suites against one Brain produced empty-trace
# FAILs (~360s, tools=None) — not a model signal.
$WavePlans = @(
    @(
        @{ n="live_check"; t=30; a=@("evals.runner","--suite","live_check","--live","--judge","--judge-model",$Judge,"--skip-preflight") },
        @{ n="tool_selection"; t=20; a=@("evals.runner","--suite","tool_selection","--concurrency","3") },
        @{ n="prompt_audit"; t=10; a=@("evals.runner","--prompt-audit") }
    ),
    @(
        @{ n="church_safe_live"; t=50; a=@("evals.runner","--suite","church_safe_live","--live","--judge","--judge-model",$Judge,"--skip-preflight") },
        @{ n="tool_selection_b"; t=20; a=@("evals.runner","--suite","tool_selection","--concurrency","3") },
        @{ n="prompt_audit_b"; t=10; a=@("evals.runner","--prompt-audit") }
    ),
    @(
        @{ n="browser_golden"; t=55; a=@("evals.runner","--suite","browser_golden","--live","--judge","--judge-model",$Judge,"--skip-preflight") },
        @{ n="tool_selection_c"; t=20; a=@("evals.runner","--suite","tool_selection","--concurrency","3") },
        @{ n="prompt_audit_c"; t=10; a=@("evals.runner","--prompt-audit") }
    ),
    @(
        @{ n="workflows_l6"; t=55; a=@("evals.runner","--suite","workflows","--workflow","--judge","--judge-model",$Judge,"--limit","6","--skip-preflight") },
        @{ n="tool_selection_d"; t=20; a=@("evals.runner","--suite","tool_selection","--concurrency","3") },
        @{ n="prompt_audit_d"; t=10; a=@("evals.runner","--prompt-audit") }
    ),
    @(
        @{ n="browser_one"; t=25; a=@("evals.runner","--suite","browser_one","--live","--judge","--judge-model",$Judge,"--skip-preflight") },
        @{ n="tool_selection_e"; t=20; a=@("evals.runner","--suite","tool_selection","--concurrency","3") },
        @{ n="prompt_audit_e"; t=10; a=@("evals.runner","--prompt-audit") }
    )
)

function Start-JobSpec($waveDir, $spec) {
    $log = Join-Path $waveDir ("{0}.log" -f $spec.n)
    $err = $log + ".err"
    "" | Set-Content $log -Encoding utf8
    Set-EvalEnv
    $args = @("-u","-m") + $spec.a
    $proc = Start-Process -FilePath $py -ArgumentList $args -WorkingDirectory $wt `
        -RedirectStandardOutput $log -RedirectStandardError $err `
        -PassThru -WindowStyle Hidden
    return @{ name=$spec.n; proc=$proc; log=$log; err=$err; timeoutMin=$spec.t; started=(Get-Date) }
}

Log ("=== loop_2h start until {0} ===" -f $deadline.ToString("o"))
if (-not (Health-Ok)) { Log "ABORT: brain 8788 down"; exit 2 }

$waveIdx = 0
foreach ($plan in $WavePlans) {
    if ((Get-Date) -ge $deadline) { Log "deadline before next wave"; break }
    $waveIdx++
    $waveDir = Join-Path $waves ("wave{0}_{1}" -f $waveIdx, (Get-Date -Format "HHmmss"))
    New-Item -ItemType Directory -Force -Path $waveDir | Out-Null
    Log ("=== WAVE {0} start ({1} jobs) dir={2} ===" -f $waveIdx, $plan.Count, $waveDir)

    $jobs = @()
    foreach ($spec in $plan) {
        if (-not (Health-Ok)) { Log "brain down mid-wave - stop launching"; break }
        $jobs += Start-JobSpec $waveDir $spec
        Log ("launched {0} pid={1}" -f $spec.n, $jobs[-1].proc.Id)
        Start-Sleep -Seconds 2
    }

    # Wait until all done or timeouts
    while ($true) {
        $alive = @($jobs | Where-Object { -not $_.proc.HasExited })
        if ($alive.Count -eq 0) { break }
        if ((Get-Date) -ge $deadline) {
            foreach ($j in $alive) {
                Log ("DEADLINE kill {0}" -f $j.name)
                try { taskkill /PID $j.proc.Id /T /F 2>$null | Out-Null } catch {}
            }
            break
        }
        foreach ($j in $alive) {
            $mins = ((Get-Date) - $j.started).TotalMinutes
            if ($mins -ge $j.timeoutMin) {
                Log ("TIMEOUT kill {0} after {1}m" -f $j.name, [int]$mins)
                try { taskkill /PID $j.proc.Id /T /F 2>$null | Out-Null } catch {}
            }
            # stall: no log growth 12m after 3m runtime
            $sz = 0
            if (Test-Path $j.log) { $sz = (Get-Item $j.log).Length }
            if (Test-Path $j.err) { $sz += (Get-Item $j.err).Length }
            if (-not $j.ContainsKey("lastSize")) { $j.lastSize = -1; $j.stallSince = Get-Date }
            if ($sz -ne $j.lastSize) { $j.lastSize = $sz; $j.stallSince = Get-Date }
            elseif ($mins -ge 3 -and ((Get-Date) - $j.stallSince).TotalMinutes -ge 12) {
                Log ("STALL kill {0} size={1}" -f $j.name, $sz)
                try { taskkill /PID $j.proc.Id /T /F 2>$null | Out-Null } catch {}
            }
        }
        Start-Sleep -Seconds 20
    }

    foreach ($j in $jobs) {
        $code = $null
        try { $j.proc.Refresh(); $code = $j.proc.ExitCode } catch {}
        Log ("WAVE {0} job {1} exit={2} log={3}" -f $waveIdx, $j.name, $code, $j.log)
        # Snapshot last 30 lines into wave summary
        $sum = Join-Path $waveDir ("{0}_tail.txt" -f $j.name)
        if (Test-Path $j.log) { Get-Content $j.log -Tail 40 | Set-Content $sum -Encoding utf8 }
    }
    Log ("=== WAVE {0} end - agent should read tails + fix harness if VOID/cluster ===" -f $waveIdx)
}

Log "=== loop_2h finished ==="
