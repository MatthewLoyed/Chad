<#
.SYNOPSIS
  Seed the browser_agent's dedicated Chrome automation profile from your real Chrome profile.

.DESCRIPTION
  Playwright's persistent context always uses the "Default" profile inside the
  --user-data-dir it is given (see config/config.yaml -> mcp_servers.playwright).
  We point that at a DEDICATED automation profile (default: %LOCALAPPDATA%\ChadBrowser\UserData)
  so the agent never fights your normal Chrome for the profile lock.

  This script copies the login-bearing state (cookies, localStorage, saved logins, the
  DPAPI key in Local State) from your chosen source profile into the automation profile.

  Safety:
  - Chrome / Playwright MCP must be fully closed (otherwise copy is partial and auth is lost).
  - Refuses to overwrite ChadBrowser when that would DROP Supabase auth tokens the
    destination already has (no-downgrade). Use -Force to override.
  - Always snapshots ChadBrowser to UserData.backup before mutating, so a bad seed
    can be restored.

.PARAMETER SourceProfile
  Which real Chrome profile to clone logins from (folder name under "User Data").
  Default: "Default".

.PARAMETER Force
  Allow overwriting ChadBrowser even when the source has fewer auth tokens.

.EXAMPLE
  # Close Chrome fully first (tray too), then:
  powershell -ExecutionPolicy Bypass -File scripts\maintenance\seed_chrome_profile.ps1
#>
param(
    [string]$SourceProfile = "Default",
    [string]$DestUserData  = (Join-Path $env:LOCALAPPDATA "ChadBrowser\UserData"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$src = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
$srcProfile = Join-Path $src $SourceProfile
$dstProfile = Join-Path $DestUserData "Default"
$backupRoot = Join-Path $env:LOCALAPPDATA "ChadBrowser\UserData.backup"

if (-not (Test-Path $srcProfile)) { throw "Source profile not found: $srcProfile" }

# Never seed FROM ChadBrowser into itself, and never treat real Chrome as dest.
if ($DestUserData -match 'Google\\Chrome\\User Data') {
    throw "Refusing to use real Chrome User Data as destination. Dest must be ChadBrowser."
}

function Get-SbAuthTokens([string]$LevelDbDir) {
    if (-not (Test-Path $LevelDbDir)) { return @() }
    $found = [System.Collections.Generic.HashSet[string]]::new()
    Get-ChildItem $LevelDbDir -File -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
            $text = [System.Text.Encoding]::ASCII.GetString($bytes)
            [regex]::Matches($text, 'sb-[a-z0-9]+-auth-token') | ForEach-Object {
                [void]$found.Add($_.Value)
            }
        } catch { }
    }
    return @($found)
}

function Get-DirBytes([string]$Path) {
    if (-not (Test-Path $Path)) { return [int64]0 }
    $sum = (Get-ChildItem $Path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return [int64]0 }
    return [int64]$sum
}

function Backup-ChadBrowser([string]$UserData, [string]$BackupRoot) {
    if (-not (Test-Path $UserData)) { return $false }
    if (Test-Path $BackupRoot) { Remove-Item $BackupRoot -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path $BackupRoot -Parent) | Out-Null
    Copy-Item $UserData $BackupRoot -Recurse -Force
    Write-Host ("  Backup -> {0}" -f $BackupRoot) -ForegroundColor DarkGray
    return $true
}

function Restore-ChadBrowser([string]$UserData, [string]$BackupRoot) {
    if (-not (Test-Path $BackupRoot)) {
        Write-Host "  No backup to restore." -ForegroundColor Yellow
        return
    }
    if (Test-Path $UserData) { Remove-Item $UserData -Recurse -Force }
    Copy-Item $BackupRoot $UserData -Recurse -Force
    Write-Host ("  Restored ChadBrowser from {0}" -f $BackupRoot) -ForegroundColor Yellow
}

# Refuse while Chrome or Playwright MCP is open.
$chrome = @(Get-Process chrome -ErrorAction SilentlyContinue)
$pwMcp = @(Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match '@playwright/mcp|playwright\\mcp') })
if ($chrome.Count -gt 0 -or $pwMcp.Count -gt 0) {
    Write-Host ""
    Write-Host ("REFUSING TO SEED - locks still held (chrome={0}, playwright-mcp={1})." -f $chrome.Count, $pwMcp.Count) -ForegroundColor Red
    Write-Host "Close Chrome fully and stop any Playwright MCP, then re-run." -ForegroundColor Yellow
    Write-Host "Or: .\scripts\maintenance\kill_chad_zombies.ps1" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$srcLs = Join-Path $srcProfile "Local Storage\leveldb"
$dstLs = Join-Path $dstProfile "Local Storage\leveldb"
$srcTokens = @(Get-SbAuthTokens $srcLs)
$dstTokensBefore = @(Get-SbAuthTokens $dstLs)

Write-Host ("Source profile: {0}" -f $srcProfile) -ForegroundColor Cyan
if ($srcTokens.Count -eq 0) {
    Write-Host "WARNING: no sb-*-auth-token keys in source Local Storage." -ForegroundColor Yellow
} else {
    Write-Host ("  Found {0} Supabase auth token key(s): {1}" -f $srcTokens.Count, ($srcTokens -join ', ')) -ForegroundColor Green
}

Write-Host ("Destination: {0}" -f $DestUserData) -ForegroundColor Cyan
if ($dstTokensBefore.Count -gt 0) {
    Write-Host ("  ChadBrowser already has {0} token(s): {1}" -f $dstTokensBefore.Count, ($dstTokensBefore -join ', ')) -ForegroundColor Green
}

# No-downgrade: refuse to wipe tokens ChadBrowser has that source lacks.
$wouldLose = @($dstTokensBefore | Where-Object { $srcTokens -notcontains $_ })
if ($wouldLose.Count -gt 0 -and -not $Force) {
    Write-Host ""
    Write-Host "REFUSING TO SEED - would DROP ChadBrowser auth tokens not present in source:" -ForegroundColor Red
    $wouldLose | ForEach-Object { Write-Host ("  - {0}" -f $_) -ForegroundColor Yellow }
    Write-Host "Source has fewer/different sessions. Re-seed would lose ChadBrowser-only logins." -ForegroundColor Yellow
    Write-Host "Sign into those sites in Chrome ($SourceProfile) first, or pass -Force to overwrite." -ForegroundColor Yellow
    Write-Host ""
    exit 3
}
if ($srcTokens.Count -eq 0 -and $dstTokensBefore.Count -gt 0 -and -not $Force) {
    Write-Host ""
    Write-Host "REFUSING TO SEED - source has no Supabase tokens but ChadBrowser does." -ForegroundColor Red
    Write-Host "This would wipe a working ChadBrowser session. Pass -Force to override." -ForegroundColor Yellow
    Write-Host ""
    exit 3
}

$srcLsBytes = Get-DirBytes (Join-Path $srcProfile "Local Storage")
Write-Host ("  Source Local Storage size: {0} bytes" -f $srcLsBytes)

# Snapshot current ChadBrowser so we can roll back if verification fails.
$hadBackup = Backup-ChadBrowser $DestUserData $backupRoot

New-Item -ItemType Directory -Force -Path (Join-Path $dstProfile "Network") | Out-Null

$localState = Join-Path $src "Local State"
if (-not (Test-Path $localState)) { throw "Missing Local State at $localState" }
Copy-Item $localState (Join-Path $DestUserData "Local State") -Force
Write-Host "  OK  Local State" -ForegroundColor Green

$files = @("Network\Cookies", "Login Data", "Web Data", "Preferences")
foreach ($f in $files) {
    $s = Join-Path $srcProfile $f
    $d = Join-Path $dstProfile $f
    if (Test-Path $s) {
        $parent = Split-Path $d -Parent
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        Copy-Item $s $d -Force
        $len = (Get-Item $d).Length
        Write-Host ("  OK  {0}  ({1} bytes)" -f $f, $len) -ForegroundColor Green
    } else {
        Write-Host ("  MISS {0}" -f $f) -ForegroundColor Yellow
    }
}

foreach ($d in @("Local Storage", "Session Storage", "IndexedDB")) {
    $s = Join-Path $srcProfile $d
    $dest = Join-Path $dstProfile $d
    if (Test-Path $s) {
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Copy-Item $s $dstProfile -Recurse -Force
        $bytes = Get-DirBytes $dest
        Write-Host ("  OK  {0}\  ({1} bytes)" -f $d, $bytes) -ForegroundColor Green
    } else {
        Write-Host ("  MISS {0}\" -f $d) -ForegroundColor Yellow
    }
}

$dstTokens = @(Get-SbAuthTokens (Join-Path $dstProfile "Local Storage\leveldb"))
$dstLsBytes = Get-DirBytes (Join-Path $dstProfile "Local Storage")
Write-Host ""
Write-Host "Verification:" -ForegroundColor Cyan
Write-Host ("  Dest Local Storage: {0} bytes (source was {1})" -f $dstLsBytes, $srcLsBytes)

$lostAfter = @($dstTokensBefore | Where-Object { $dstTokens -notcontains $_ })
if ($srcTokens.Count -gt 0 -and $dstTokens.Count -eq 0) {
    Write-Host "  FAIL: source had tokens but destination does not. Restoring backup." -ForegroundColor Red
    if ($hadBackup) { Restore-ChadBrowser $DestUserData $backupRoot }
    exit 2
}
if ($lostAfter.Count -gt 0) {
    Write-Host "  FAIL: seed dropped ChadBrowser tokens. Restoring backup." -ForegroundColor Red
    $lostAfter | ForEach-Object { Write-Host ("  - lost {0}" -f $_) -ForegroundColor Yellow }
    if ($hadBackup) { Restore-ChadBrowser $DestUserData $backupRoot }
    exit 2
}
if ($dstTokens.Count -gt 0) {
    Write-Host ("  OK  Supabase auth tokens in ChadBrowser: {0}" -f ($dstTokens -join ', ')) -ForegroundColor Green
} else {
    Write-Host "  OK  copy finished, but no Supabase tokens on either side." -ForegroundColor Yellow
}

if ($srcLsBytes -gt 0 -and $dstLsBytes -lt ($srcLsBytes * 0.5)) {
    Write-Host "  WARN: dest Local Storage is <50% of source size - copy may be incomplete." -ForegroundColor Yellow
}

Write-Host ""
Write-Host ("Seeded automation profile at: {0} (from '{1}')" -f $DestUserData, $SourceProfile) -ForegroundColor Cyan
Write-Host ("Previous ChadBrowser snapshot: {0}" -f $backupRoot) -ForegroundColor DarkGray
Write-Host "You can reopen Chrome now." -ForegroundColor Cyan
Write-Host ("Quick check: chrome.exe --user-data-dir=`"{0}`" https://supabase.com/dashboard" -f $DestUserData) -ForegroundColor DarkGray
