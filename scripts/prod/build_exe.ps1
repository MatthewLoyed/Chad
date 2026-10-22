<#
.SYNOPSIS
    Build the Chad desktop GUI and/or headless Brain into packaged apps:
    dist\Chad\Chad.exe and dist\Chad-Brain\Chad-Brain.exe.

.DESCRIPTION
    One-folder PyInstaller builds:
      - GUI (Limb): main_gui.py, chad.ico embedded, production React UI bundled
        inside. Connects to a Brain over WebSocket exactly like the
        script-launched GUI does; auto-starts a local Brain if none is
        reachable (see src/remote/limb.py).
      - Brain: src/remote/server.py, headless (no window). No voice-mode
        extras bundled (faster-whisper/kokoro/torch) - the headless Brain has
        no audio.

    Both frozen apps resolve config/, data/, profiles.yaml etc. via
    CHAD_HOME, pinned by a chad_home.txt written next to each exe (see
    src/shared/frozen_bootstrap.py) - a normal source checkout still
    provides all of that; freezing doesn't change what backs it.

    Steps:
      1. Build the web UI (npm run build) unless -SkipWeb and a dist already
         exists (GUI build only).
      2. Run PyInstaller with the selected spec(s).
      3. Write chad_home.txt (absolute repo path) next to each exe.

    Output: <repo>\dist\Chad\Chad.exe and/or <repo>\dist\Chad-Brain\Chad-Brain.exe

.PARAMETER Gui
    Build only the GUI (default when no switch is passed).

.PARAMETER Brain
    Build only the headless Brain.

.PARAMETER All
    Build both the GUI and the Brain.

.PARAMETER SkipWeb
    Skip the `npm run build` step (use the existing src/clients/web/dist).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prod\build_exe.ps1 -All
#>
param(
    [switch]$Gui,
    [switch]$Brain,
    [switch]$All,
    [switch]$SkipWeb,
    # Override the Python interpreter (defaults to <repo>\.venv). Useful from a
    # git worktree, where the .venv lives in the main checkout. Can also be set
    # via the CHAD_PYTHON environment variable.
    [string]$Python
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Python) { $Python = $env:CHAD_PYTHON }
if (-not $Python) { $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe" }
$WebDir   = Join-Path $RepoRoot "src\clients\web"
$DistRoot = Join-Path $RepoRoot "dist"
$WorkDir  = Join-Path $RepoRoot "build\pyinstaller"

# Default to GUI-only when nothing is specified (preserves prior behavior).
if (-not $Gui -and -not $Brain -and -not $All) { $Gui = $true }
if ($All) { $Gui = $true; $Brain = $true }

Write-Host "=== Build Chad (Gui=$Gui Brain=$Brain) ===" -ForegroundColor Green
Write-Host "Repo: $RepoRoot"

if (-not (Test-Path $Python)) { throw "venv python not found at $Python" }

function Write-ChadHome([string]$AppDir) {
    [System.IO.File]::WriteAllText(
        (Join-Path $AppDir "chad_home.txt"), $RepoRoot, (New-Object System.Text.UTF8Encoding($false)))
}

function Build-PyInstaller([string]$SpecName) {
    $Spec = Join-Path $RepoRoot "packaging\$SpecName"
    Write-Host "[..] Running PyInstaller ($SpecName, this can take a few minutes)..." -ForegroundColor Cyan
    $env:PYTHONPATH = $RepoRoot
    & $Python -m PyInstaller $Spec --noconfirm --clean `
        --distpath $DistRoot --workpath $WorkDir
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed for $SpecName (exit $LASTEXITCODE)" }
}

if ($Gui) {
    # --- Web UI build (GUI only) ---
    $WebDist = Join-Path $WebDir "dist\index.html"
    if ($SkipWeb -and (Test-Path $WebDist)) {
        Write-Host "[--] Skipping web build (using existing dist)." -ForegroundColor DarkGray
    } else {
        Write-Host "[..] Building web UI (npm run build)..." -ForegroundColor Cyan
        Push-Location $WebDir
        try {
            if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
                npm install
                if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
            }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        } finally {
            Pop-Location
        }
    }
    if (-not (Test-Path $WebDist)) { throw "Web build missing: $WebDist" }

    Build-PyInstaller "chad_gui.spec"
    $GuiAppDir = Join-Path $DistRoot "Chad"
    $GuiExe = Join-Path $GuiAppDir "Chad.exe"
    if (-not (Test-Path $GuiExe)) { throw "Build finished but $GuiExe is missing" }
    Write-ChadHome $GuiAppDir
    Write-Host "[OK] Built: $GuiExe" -ForegroundColor Green
}

if ($Brain) {
    Build-PyInstaller "chad_brain.spec"
    $BrainAppDir = Join-Path $DistRoot "Chad-Brain"
    $BrainExe = Join-Path $BrainAppDir "Chad-Brain.exe"
    if (-not (Test-Path $BrainExe)) { throw "Build finished but $BrainExe is missing" }
    Write-ChadHome $BrainAppDir
    Write-Host "[OK] Built: $BrainExe" -ForegroundColor Green
}

Write-Host ""
Write-Host "[OK] chad_home.txt -> $RepoRoot (written next to each built exe)" -ForegroundColor Green
Write-Host "Run dist\Chad\Chad.exe directly to test - it auto-starts dist\Chad-Brain\Chad-Brain.exe" -ForegroundColor Green
Write-Host "if no Brain is already reachable. Re-run scripts\prod\install_prod.ps1 to create the" -ForegroundColor Green
Write-Host "Start-menu / desktop entries that point at the GUI exe." -ForegroundColor Green
