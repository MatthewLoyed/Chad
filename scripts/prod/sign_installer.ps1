<#
.SYNOPSIS
    Code-sign a built exe with signtool.exe, if a certificate is configured (issue #430).

.DESCRIPTION
    No-op by design until Chad has a real code-signing certificate: if
    CHAD_SIGN_PFX_BASE64 (or -PfxPath) isn't set, this prints a warning and
    returns success so build_installer.ps1 / release.yml keep working unsigned
    exactly as they do today. Wire it up later by setting these as GitHub
    Actions secrets (or local env vars) - no script changes needed:

      CHAD_SIGN_PFX_BASE64   base64 of the .pfx certificate file
      CHAD_SIGN_PFX_PASSWORD the .pfx's password
      CHAD_SIGN_TIMESTAMP_URL  optional, defaults to DigiCert's RFC3161 server

.PARAMETER FilePath
    Path to the .exe to sign (in place).

.PARAMETER PfxPath
    Local .pfx path instead of decoding CHAD_SIGN_PFX_BASE64 (for testing with
    a cert already on disk).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prod\sign_installer.ps1 -FilePath dist\Chad-Setup-1.0.0.exe
#>
param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string]$PfxPath
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $FilePath)) { throw "File not found: $FilePath" }

$PfxBase64 = $env:CHAD_SIGN_PFX_BASE64
$PfxPassword = $env:CHAD_SIGN_PFX_PASSWORD
$TimestampUrl = $env:CHAD_SIGN_TIMESTAMP_URL
if (-not $TimestampUrl) { $TimestampUrl = "http://timestamp.digicert.com" }

if (-not $PfxPath -and -not $PfxBase64) {
    Write-Host "[--] No signing certificate configured (CHAD_SIGN_PFX_BASE64 unset)." -ForegroundColor Yellow
    Write-Host "     Leaving $FilePath unsigned. See scripts\prod\sign_installer.ps1 header to enable (#430)." -ForegroundColor Yellow
    exit 0
}

$SignTool = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "x64" } | Select-Object -First 1 -ExpandProperty FullName
if (-not $SignTool) {
    throw "signtool.exe not found. Install the Windows SDK (winget install Microsoft.WindowsSDK) or a build environment that includes it."
}

$TempPfx = $null
try {
    if (-not $PfxPath) {
        $TempPfx = Join-Path ([System.IO.Path]::GetTempPath()) "chad-sign-$(New-Guid).pfx"
        [System.IO.File]::WriteAllBytes($TempPfx, [Convert]::FromBase64String($PfxBase64))
        $PfxPath = $TempPfx
    }

    Write-Host "[..] Signing $FilePath ..." -ForegroundColor Cyan
    & $SignTool sign /f $PfxPath /p $PfxPassword /fd SHA256 /tr $TimestampUrl /td SHA256 $FilePath
    if ($LASTEXITCODE -ne 0) { throw "signtool sign failed (exit $LASTEXITCODE)" }

    & $SignTool verify /pa $FilePath
    if ($LASTEXITCODE -ne 0) { throw "signtool verify failed (exit $LASTEXITCODE)" }

    Write-Host "[OK] Signed and verified: $FilePath" -ForegroundColor Green
} finally {
    if ($TempPfx -and (Test-Path $TempPfx)) { Remove-Item $TempPfx -Force }
}
