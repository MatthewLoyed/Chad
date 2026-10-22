#Requires -Version 5.1
<#
.SYNOPSIS
  First-run config bootstrap for a fresh Chad clone.

.DESCRIPTION
  Copies gitignored config templates (secrets, personal knowledge, preferences)
  when missing. Does not overwrite existing local files.

  Run from the repo root:
    .\scripts\setup\bootstrap_config.ps1
#>
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$configDir = Join-Path $repoRoot "config"

function Copy-IfMissing {
    param(
        [string]$Source,
        [string]$Dest,
        [string]$Label
    )
    if (-not (Test-Path $Source)) {
        Write-Warning "Template missing: $Source"
        return
    }
    if ((Test-Path $Dest) -and -not $Force) {
        Write-Host "[skip] $Label already exists"
        return
    }
    Copy-Item -Path $Source -Destination $Dest -Force
    Write-Host "[copy] $Label"
}

Write-Host "Chad config bootstrap ($repoRoot)"
Write-Host ""

Copy-IfMissing `
    -Source (Join-Path $configDir "secrets.yaml.example") `
    -Dest (Join-Path $configDir "secrets.yaml") `
    -Label "secrets.yaml"

Copy-IfMissing `
    -Source (Join-Path $configDir "preferences.yaml.example") `
    -Dest (Join-Path $configDir "preferences.yaml") `
    -Label "preferences.yaml"

Copy-IfMissing `
    -Source (Join-Path $configDir "personal_knowledge.md.example") `
    -Dest (Join-Path $configDir "personal_knowledge.md") `
    -Label "personal_knowledge.md"

$envExample = Join-Path $repoRoot ".env.example"
$envFile = Join-Path $repoRoot ".env"
Copy-IfMissing -Source $envExample -Dest $envFile -Label ".env"

$claudeExample = Join-Path $repoRoot "CLAUDE.md.example"
$claudeFile = Join-Path $repoRoot "CLAUDE.md"
Copy-IfMissing -Source $claudeExample -Dest $claudeFile -Label "CLAUDE.md"

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Fill config\secrets.yaml and .env with API keys (GEMINI_API_KEY minimum for cloud mode)."
Write-Host "  2. Edit config\personal_knowledge.md so Chad knows who you are (see docs/setup/PERSONAL_KNOWLEDGE.md)."
Write-Host "  3. Optional: add machine-specific apps/routines under profile: in config\preferences.yaml."
Write-Host "  4. pip install -r requirements.txt && npm run build (in src/clients/web)"
Write-Host "  5. Start the Brain: .\scripts\prod\launch_dev.ps1  (or connect to prod on :8767)"
Write-Host "  6. Optional: Chrome history sync — see docs/setup/BROWSER_HISTORY.md"
Write-Host ""
Write-Host "Supabase is optional — Chad boots with bundled persona fallback. See docs/setup/COLD_START.md."
Write-Host "Dev workspace (gitignored): extend CLAUDE.md locally; see docs/guides/DEV_WORKSPACE.md."
