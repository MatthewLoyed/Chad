<#
.SYNOPSIS
    Stops the dev stack (Vite + dev Brain + dev GUI) started by launch_dev.ps1.
#>

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_chad_paths.ps1")
$SelfRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$MainRoot = Get-ChadMainRepoRoot -FromPath $SelfRoot
$DevRoot  = Get-ChadDevRepoRoot -MainRoot $MainRoot
$PidFile  = Join-Path $DevRoot "data\dev_logs\dev_stack.pids"

# Legacy pid file location (pre-worktree)
$LegacyPidFile = Join-Path $MainRoot "data\dev_logs\dev_stack.pids"

foreach ($file in @($PidFile, $LegacyPidFile)) {
    if (-not (Test-Path $file)) { continue }
    Get-Content $file | ForEach-Object {
        $procId = $_.Trim()
        if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
            taskkill /PID $procId /T /F 2>$null | Out-Null
        }
    }
    Remove-Item $file -Force -ErrorAction SilentlyContinue
}

foreach ($port in @(5173, 8788)) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { taskkill /PID $_ /T /F 2>$null | Out-Null }
}

# The dev GUI (main_gui.py in the dev worktree) is not on a port, so a stale
# pid file — e.g. two launches racing, or a relaunch watchdog respawn — can
# leave orphaned dev windows behind. Worse, a dev GUI relaunched without
# CHAD_SERVER_URL falls back to the prod Brain on :8767 and then shows master,
# which looks like a broken branch switch. Kill any GUI whose command line
# points at the dev worktree so a fresh launch is always the only dev window.
# never touched.
$devRootLeaf = Split-Path $DevRoot -Leaf
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$devRootLeaf*main_gui.py*" } |
    ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }

# Stop any orphan ngrok tunnels pointing at the dev port (8788)
Get-CimInstance Win32_Process -Filter "Name='ngrok.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*8788*" } |
    ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }

Write-Host "[OK] Dev stack stopped." -ForegroundColor Green
