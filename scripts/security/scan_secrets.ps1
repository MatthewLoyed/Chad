# Run gitleaks + trufflehog secret scans (issue #404).
# Usage (from repo root):
#   .\scripts\security\scan_secrets.ps1
#   .\scripts\security\scan_secrets.ps1 -HistoryOnly

param(
    [switch]$HistoryOnly,
    [string]$ReportDirectory = (Join-Path $env:LOCALAPPDATA "Chad\SecurityReports")
)

$ErrorActionPreference = "Stop"
$RepoRoot = git rev-parse --show-toplevel
$ToolsDir = Join-Path $RepoRoot ".tools"
$Config = Join-Path $RepoRoot ".gitleaks.toml"
$ReportDirectory = [IO.Path]::GetFullPath($ReportDirectory)
if ($ReportDirectory.StartsWith($RepoRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or $ReportDirectory -eq $RepoRoot) {
    throw "Secret reports must be stored outside the repository"
}
New-Item -ItemType Directory -Force -Path $ReportDirectory | Out-Null

function Ensure-Tool {
    param([string]$Name, [string]$Url, [string]$ArchiveName, [switch]$IsTarGz)

    $bin = Join-Path $ToolsDir "$Name.exe"
    if (Test-Path $bin) { return $bin }

    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    $archive = Join-Path $ToolsDir $ArchiveName

    Write-Host "Downloading $Name..."
    Invoke-WebRequest -Uri $Url -OutFile $archive
    if ($IsTarGz) {
        tar -xzf $archive -C $ToolsDir
    } else {
        Expand-Archive -Path $archive -DestinationPath $ToolsDir -Force
    }
    if (-not (Test-Path $bin)) {
        throw "Expected binary not found after extract: $bin"
    }
    return $bin
}

$gitleaks = Ensure-Tool -Name "gitleaks" `
    -Url "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_windows_x64.zip" `
    -ArchiveName "gitleaks.zip"

$trufflehog = Ensure-Tool -Name "trufflehog" `
    -Url "https://github.com/trufflesecurity/trufflehog/releases/download/v3.90.4/trufflehog_3.90.4_windows_amd64.tar.gz" `
    -ArchiveName "trufflehog.tar.gz" `
    -IsTarGz

$fail = $false

if (-not $HistoryOnly) {
    Write-Host "`n=== gitleaks: working tree ===" -ForegroundColor Cyan
    & $gitleaks detect --source=$RepoRoot --no-git --config=$Config --no-banner --redact -v
    if ($LASTEXITCODE -ne 0) { $fail = $true }

    Write-Host "`n=== trufflehog: working tree ===" -ForegroundColor Cyan
    # Do not send discovered credentials to providers for verification or print
    # raw scanner JSON into terminal/chat logs. Unverified findings still fail.
    & $trufflehog filesystem $RepoRoot --no-verification --no-update --fail --json *> (Join-Path $ReportDirectory "trufflehog-tree.jsonl")
    if ($LASTEXITCODE -ne 0) { $fail = $true }
}

Write-Host "`n=== gitleaks: full git history ===" -ForegroundColor Cyan
& $gitleaks detect --source=$RepoRoot --config=$Config --no-banner --redact --log-opts="--all" -v
if ($LASTEXITCODE -ne 0) { $fail = $true }

Write-Host "`n=== trufflehog: full git history (GitHub remote) ===" -ForegroundColor Cyan
$origin = git -C $RepoRoot remote get-url origin
& $trufflehog git $origin --no-verification --no-update --fail --json *> (Join-Path $ReportDirectory "trufflehog-remote.jsonl")
if ($LASTEXITCODE -ne 0) { $fail = $true }

if ($fail) {
    Write-Host "`nSECRET SCAN FAILED — triage findings above." -ForegroundColor Red
    Write-Host "Review private reports in $ReportDirectory. Rotate real credentials and agree on a history-cleanup plan before rewriting or force-pushing."
    exit 1
}

Write-Host "`nSECRET SCAN PASSED — no findings." -ForegroundColor Green
exit 0
