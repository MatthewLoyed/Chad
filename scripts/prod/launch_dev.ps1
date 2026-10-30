<#
.SYNOPSIS
    One-click dev stack: Vite + dev Brain (8788) + dev GUI - from git worktree.

.DESCRIPTION
    Chad Dev runs from ../AIAssistant-dev (git worktree). Prod Chad in the main
    repo stays on master untouched. Branch switching in Settings only affects
    the worktree checkout.

.NOTES
    Prod runs alongside on 8767 - different port, different folder, no collision.
#>

param(
    # Skip the dist/ build (step 1c). Faster launch when you only care about the desktop
    # GUI, which loads from Vite; phone/PWA access keeps whatever dist/ was last built.
    [switch]$NoWebBuild,
    # Force background daemons (heartbeat/cron/sync) off in dev instead of following preferences.yaml
    [switch]$DisableDaemons,
    # Skip launching ngrok even if telegram.public_base_url is configured
    [switch]$NoNgrok,
    # Run code/assets from this checkout instead of redirecting to the legacy sibling.
    [switch]$CurrentCheckout
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_chad_paths.ps1")
$SelfRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$MainRoot = if ($env:CHAD_MAIN_ROOT -and (Test-Path $env:CHAD_MAIN_ROOT)) {
    (Resolve-Path $env:CHAD_MAIN_ROOT).Path
} else {
    Get-ChadMainRepoRoot -FromPath $SelfRoot
}
$setupOptions = @{}
if ($CurrentCheckout) { $setupOptions.DevRoot = $SelfRoot }
$DevRoot = & (Join-Path $PSScriptRoot "setup_dev_worktree.ps1") @setupOptions | Select-Object -Last 1
if (-not (Test-Path $DevRoot)) {
    Write-Host "[FAIL] Dev worktree setup failed." -ForegroundColor Red
    exit 1
}

# If launched from the main repo, delegate to the dev worktree's own launch_dev.ps1
# so edits to dev launcher (daemons, tunnels, flags) take effect immediately without
# needing a merge to master first.
if ($SelfRoot -ne $DevRoot -and (Test-Path (Join-Path $DevRoot "scripts\prod\launch_dev.ps1"))) {
    & (Join-Path $DevRoot "scripts\prod\launch_dev.ps1") @PSBoundParameters
    exit $LASTEXITCODE
}

$DevPort    = 8788
$WebDir     = Join-Path $DevRoot "src\clients\web"
$LogDir     = Join-Path $DevRoot "data\dev_logs"
$PidFile    = Join-Path $LogDir "dev_stack.pids"
$stamp      = Get-Date -Format "yyyyMMdd"
$VenvPython = Join-Path $MainRoot ".venv\Scripts\python.exe"
$VenvPythonw = Join-Path $MainRoot ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# --- 1. Stop any previously running dev stack ---
& (Join-Path $PSScriptRoot "stop_dev_stack.ps1")

# --- 1b. Web deps if node_modules junction missing ---
$nodeModules = Join-Path $WebDir "node_modules"
if (-not (Test-Path $nodeModules)) {
    Write-Host "Installing web deps in worktree ..." -ForegroundColor DarkGray
    $npmOut = Join-Path $LogDir "npm_$stamp.log"
    $npmErr = Join-Path $LogDir "npm_$stamp.err.log"
    $npmProc = Start-Process -FilePath "npm.cmd" -ArgumentList @("ci") `
        -WorkingDirectory $WebDir -WindowStyle Hidden -PassThru -Wait `
        -RedirectStandardOutput $npmOut -RedirectStandardError $npmErr
    if ($npmProc.ExitCode -ne 0) {
        Write-Host "[--] npm ci failed - see $npmErr" -ForegroundColor Yellow
    }
}

# --- 1c. Build dist/ for phone/PWA access (background) ---
# The desktop GUI loads from Vite (hot reload), but a phone can't use Vite: the PWA
# plugin sets `devOptions.enabled: false`, so no service worker registers in dev mode
# and the app can never be installed to a home screen. The Brain serves the built
# bundle from src/clients/web/dist instead (endpoints.py), reached over HTTPS via
# `tailscale serve`.
#
# Runs in the background without blocking the desktop GUI launch.
$buildProc = $null
if (-not $NoWebBuild) {
    Write-Host "Building web bundle for phone/PWA access (background) ..." -ForegroundColor Cyan
    $buildOut = Join-Path $LogDir "webbuild_$stamp.log"
    $buildErr = Join-Path $LogDir "webbuild_$stamp.err.log"
    $buildProc = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "build") `
        -WorkingDirectory $WebDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $buildOut -RedirectStandardError $buildErr
}

# --- 2. Vite dev server ---
Write-Host "Starting Vite (worktree: $DevRoot) ..." -ForegroundColor Cyan
$viteOut = Join-Path $LogDir "vite_$stamp.log"
$viteErr = Join-Path $LogDir "vite_$stamp.err.log"
$viteProc = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") `
    -WorkingDirectory $WebDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $viteOut -RedirectStandardError $viteErr

# --- 3. Dev Brain (8788) - code from worktree, venv from main ---
Write-Host "Starting dev Brain on port $DevPort ..." -ForegroundColor Cyan
$brainOut = Join-Path $LogDir "brain_$stamp.log"
$brainErr = Join-Path $LogDir "brain_$stamp.err.log"
# Same SQLite as prod (absolute path — do not hardlink; WAL must share one path)
$LocalDb = Get-ChadLocalDbPath -MainRoot $MainRoot
$brainEnv = @{
    PYTHONPATH           = "."
    CHAD_MODE            = "cloud"
    PORT                 = "$DevPort"
    CHAD_DEV_WORKTREE    = "1"
    CHAD_MAIN_ROOT       = $MainRoot
    CHAD_LOCAL_DB_PATH   = $LocalDb
    # Worktree has no secrets.yaml / .env — point at main so Brave/Serper/E2B/etc. resolve
    # the same as the GUI Settings / prod Brain (otherwise sandbox 401s and search falls over).
    CHAD_SECRETS_PATH    = (Join-Path $MainRoot "config\secrets.yaml")
}
if (-not $env:CHAD_LOG_LEVEL) { $brainEnv["CHAD_LOG_LEVEL"] = "INFO" }
# Do not force CHAD_DAEMONS_ENABLED="false" by default so config/preferences.yaml governs
# daemons dynamically from Settings -> Automations / Capabilities (#473 parity).
# If explicitly passed via -DisableDaemons, disable them; otherwise clear any stale env override.
if ($DisableDaemons) {
    $brainEnv["CHAD_DAEMONS_ENABLED"] = "false"
} elseif ($env:CHAD_DAEMONS_ENABLED) {
    $brainEnv["CHAD_DAEMONS_ENABLED"] = $env:CHAD_DAEMONS_ENABLED
} else {
    $brainEnv.Remove("CHAD_DAEMONS_ENABLED")
    Remove-Item "env:CHAD_DAEMONS_ENABLED" -ErrorAction SilentlyContinue
}
foreach ($k in $brainEnv.Keys) { Set-Item -Path "env:$k" -Value $brainEnv[$k] }
# Import main .env into this process so real API keys win over secrets.yaml stubs
# (e.g. BRAVE placeholder in secrets, live key in .env; E2B key drift between the two).
$mainEnvFile = Join-Path $MainRoot ".env"
if (Test-Path $mainEnvFile) {
    Get-Content $mainEnvFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or $line -notmatch "=") { return }
        $k, $v = $line.Split("=", 2)
        $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
        if ($k) { Set-Item -Path "env:$k" -Value $v }
    }
}
$brainProc = Start-Process -FilePath $VenvPython `
    -ArgumentList @(Join-Path $DevRoot "scripts\prod\run_dev_brain.py") `
    -WorkingDirectory $DevRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $brainOut -RedirectStandardError $brainErr

# --- 3b. Ngrok tunnel for Telegram / webhooks (if configured) ---
$ngrokProc = $null
if (-not $NoNgrok) {
    $ngrokUrl = & $VenvPython -c "from src.shared.config_manager import ConfigManager; u = ConfigManager.get_secret('telegram.public_base_url') or ''; print(u if 'ngrok' in u else '')" 2>$null
    $hasNgrok = Get-Command "ngrok" -ErrorAction SilentlyContinue
    if ($ngrokUrl -and $hasNgrok) {
        Write-Host "Starting ngrok tunnel for $ngrokUrl -> :$DevPort ..." -ForegroundColor Cyan
        $ngrokOut = Join-Path $LogDir "ngrok_$stamp.log"
        $ngrokErr = Join-Path $LogDir "ngrok_$stamp.err.log"
        $ngrokProc = Start-Process -FilePath "ngrok.exe" `
            -ArgumentList @("http", "$DevPort", "--url=$ngrokUrl") `
            -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $ngrokOut -RedirectStandardError $ngrokErr
        Start-Sleep -Milliseconds 800
        if ($ngrokProc -and (Get-Process -Id $ngrokProc.Id -ErrorAction SilentlyContinue)) {
            Write-Host "[OK] ngrok tunnel active ($ngrokUrl -> :$DevPort)" -ForegroundColor Green
        } else {
            Write-Host "[--] ngrok failed to start - see $ngrokErr" -ForegroundColor Yellow
            $ngrokProc = $null
        }
    }
}

# --- 4. Wait for Vite + Brain ---
function Wait-Http($url, $timeoutSec, $proc = $null) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        # If the process died, bail immediately
        if ($proc -and -not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) { return $false }
        try {
            if ((Invoke-WebRequest -Uri $url -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    # Final check: if process is alive, treat as OK (health-check may be unreachable due to session isolation)
    if ($proc -and (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) { return $true }
    return $false
}
Write-Host "Waiting for Vite + dev Brain ..." -ForegroundColor DarkGray
$viteOk  = Wait-Http "http://127.0.0.1:5173" 30 $viteProc
$brainOk = Wait-Http "http://127.0.0.1:$DevPort/health" 30 $brainProc
if (-not $viteOk)  { Write-Host "[--] Vite not up on :5173 - check $viteErr" -ForegroundColor Yellow }
if (-not $brainOk) { Write-Host "[--] Dev Brain not up on :$DevPort - check $brainErr" -ForegroundColor Yellow }

# --- 5. Dev GUI ---
Write-Host "Starting dev GUI -> ws://localhost:$DevPort/ws ..." -ForegroundColor Cyan
$env:CHAD_SERVER_URL  = "ws://localhost:$DevPort/ws"
$env:CHAD_UI_DEV      = "true"
$env:CHAD_DEV_WORKTREE = "1"
$env:CHAD_MAIN_ROOT   = $MainRoot
$env:CHAD_LOCAL_DB_PATH = $LocalDb
$guiProc = Start-Process -FilePath $VenvPythonw `
    -ArgumentList @(Join-Path $DevRoot "main_gui.py") `
    -WorkingDirectory $DevRoot -PassThru `
    -RedirectStandardOutput (Join-Path $LogDir "gui_$stamp.log") `
    -RedirectStandardError (Join-Path $LogDir "gui_$stamp.err.log")

$pids = @($viteProc.Id, $brainProc.Id, $guiProc.Id)
if ($ngrokProc) { $pids += $ngrokProc.Id }
if ($buildProc) { $pids += $buildProc.Id }
$pids | Set-Content -Path $PidFile

$branch = git -C $DevRoot branch --show-current 2>$null
Write-Host "Dev stack launched. Branch: $branch | DB: $LocalDb | Logs: $LogDir" -ForegroundColor Green
