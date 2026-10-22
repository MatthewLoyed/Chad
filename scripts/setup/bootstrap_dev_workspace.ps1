#Requires -Version 5.1
<#
.SYNOPSIS
  Bootstrap local-only dev/agent workspace files from tracked templates.

.DESCRIPTION
  Copies CLAUDE.md.example -> CLAUDE.md when missing. Does not overwrite existing local files
  unless -Force. .agent/, CONTEXT.md, TODOS.md, and opencode.json are never created here —
  those are maintainer-specific; add them locally or restore from a private backup.

  Run from the repo root:
    .\scripts\setup\bootstrap_dev_workspace.ps1
#>
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

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

Write-Host "Chad dev workspace bootstrap ($repoRoot)"
Write-Host ""

Copy-IfMissing `
    -Source (Join-Path $repoRoot "CLAUDE.md.example") `
    -Dest (Join-Path $repoRoot "CLAUDE.md") `
    -Label "CLAUDE.md"

Write-Host ""
Write-Host "Shared guidance: AGENTS.md (tracked; no RTK.md needed)"
Write-Host "Local-only paths (gitignored, not auto-created):"
Write-Host "  .agent/  CONTEXT.md  TODOS.md  opencode.json  docs/HANDOFF_*.md"
Write-Host ""
Write-Host "See docs/guides/DEV_WORKSPACE.md for backup and contributor notes."
