# Install gitleaks pre-commit hook (Windows / PowerShell).
# Run once from the repo root after cloning:  .\scripts\security\install_hooks.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = git rev-parse --show-toplevel
$HookPath = Join-Path $RepoRoot ".git\hooks\pre-commit"

if (-not (Get-Command gitleaks -ErrorAction SilentlyContinue)) {
    Write-Error @"
gitleaks is not installed.
  winget install gitleaks   (Windows Package Manager)
  scoop install gitleaks
  choco install gitleaks
"@
    exit 1
}

$HookContent = @'
#!/usr/bin/env bash
# Pre-commit: block secrets from entering git history.
gitleaks protect --staged --no-banner --config "$(git rev-parse --show-toplevel)/.gitleaks.toml"
'@

Set-Content -Path $HookPath -Value $HookContent -Encoding UTF8
Write-Host "gitleaks pre-commit hook installed at $HookPath"
Write-Host "Test it with:  gitleaks protect --staged --no-banner"
