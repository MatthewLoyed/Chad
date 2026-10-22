<#
.SYNOPSIS
    End-to-end build of Chad-Setup-<version>.exe (issue #429): PyInstaller
    builds -> installer payload staging -> NSIS compile.

.DESCRIPTION
    1. scripts\prod\build_exe.ps1 -All   (dist\Chad, dist\Chad-Brain)
    2. scripts\prod\stage_installer_payload.ps1  (dist\installer_payload)
    3. makensis packaging\chad_installer.nsi     (dist\Chad-Setup-<version>.exe)

.PARAMETER Version
    Installer version string (e.g. "1.2.0"). Defaults to the current git
    tag/describe output, or "0.0.0-dev" if not tagged.

.PARAMETER SkipBuild
    Skip step 1 (reuse an existing dist\Chad / dist\Chad-Brain).

.PARAMETER Python
    Passed through to build_exe.ps1 (see its -Python param).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prod\build_installer.ps1
    powershell -ExecutionPolicy Bypass -File scripts\prod\build_installer.ps1 -Version 1.0.0
#>
param(
    [string]$Version,
    [switch]$SkipBuild,
    [string]$Python
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DistRoot = Join-Path $RepoRoot "dist"

if (-not $Version) {
    try {
        $Version = (git -C $RepoRoot describe --tags --abbrev=0 2>$null) -replace '^v', ''
    } catch {}
    if (-not $Version) { $Version = "0.0.0-dev" }
}

$Makensis = "C:\Program Files (x86)\NSIS\makensis.exe"
if (-not (Test-Path $Makensis)) {
    $Makensis = "C:\Program Files\NSIS\makensis.exe"
}
if (-not (Test-Path $Makensis)) {
    throw "makensis.exe not found. Install NSIS: winget install NSIS.NSIS"
}

Write-Host "=== Build Chad installer v$Version ===" -ForegroundColor Green

if (-not $SkipBuild) {
    $buildArgs = @{ All = $true }
    if ($Python) { $buildArgs["Python"] = $Python }
    & (Join-Path $PSScriptRoot "build_exe.ps1") @buildArgs
} else {
    Write-Host "[--] Skipping PyInstaller build (reusing existing dist\Chad, dist\Chad-Brain)." -ForegroundColor DarkGray
}

& (Join-Path $PSScriptRoot "stage_installer_payload.ps1")

$PayloadDir = Join-Path $DistRoot "installer_payload"
$OutFile = Join-Path $DistRoot "Chad-Setup-$Version.exe"
$NsiScript = Join-Path $RepoRoot "packaging\chad_installer.nsi"

Write-Host "[..] Compiling installer with NSIS..." -ForegroundColor Cyan
& $Makensis "/DVERSION=$Version" "/DPAYLOAD_DIR=$PayloadDir" "/DOUT_FILE=$OutFile" $NsiScript
if ($LASTEXITCODE -ne 0) { throw "makensis failed (exit $LASTEXITCODE)" }

if (-not (Test-Path $OutFile)) { throw "Build finished but $OutFile is missing" }
Write-Host ""
Write-Host "[OK] Built: $OutFile" -ForegroundColor Green

& (Join-Path $PSScriptRoot "sign_installer.ps1") -FilePath $OutFile
