#Requires -Version 5.1
<#
.SYNOPSIS
  Fail if tracked config contains machine-specific personal paths or deployment IDs.

  Run from repo root (CI or local):
    .\scripts\setup\audit_tracked_config.ps1
#>
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot

$patterns = @(
    @{ Name = "Windows user profile path"; Regex = "C:\\Users\\[^\\%]+" },
    @{ Name = "XboxGames install path"; Regex = "XboxGames" },
    @{ Name = "Supabase project URL"; Regex = "https://[a-z0-9]+\.supabase\.co" },
    @{ Name = "Hubspace fast-track"; Regex = "hubspace_control" },
    @{ Name = "Render deployment URL"; Regex = "onrender\.com" }
)

$files = @(
    "config/config.yaml",
    "config/profiles.yaml"
)

$failed = $false
foreach ($file in $files) {
    if (-not (Test-Path $file)) {
        Write-Warning "Missing $file"
        continue
    }
    $content = Get-Content $file -Raw
    foreach ($pattern in $patterns) {
        if ($content -match $pattern.Regex) {
            Write-Error "[FAIL] $($pattern.Name) found in $file"
            $failed = $true
        }
    }
}

if ($failed) {
    exit 1
}

Write-Host "[ok] Tracked config audit passed ($($files -join ', '))"
