param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$TestRoot
)
$ErrorActionPreference = 'Stop'
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$TestRoot = [IO.Path]::GetFullPath($TestRoot)
if (Test-Path -LiteralPath $TestRoot) { throw 'Use a new isolated test directory' }
New-Item -ItemType Directory -Path (Join-Path $TestRoot 'data') -Force | Out-Null
$marker = Join-Path $TestRoot 'data\release-smoke-marker.txt'
Set-Content -LiteralPath $marker -Value 'Synthetic release acceptance data'
# NSIS requires /D to be last and unquoted. All installs stay in this explicit root.
foreach ($attempt in 1..2) {
    $process = Start-Process -FilePath $Installer -ArgumentList "/S /D=$TestRoot" -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
    if ((Get-Content -LiteralPath $marker -Raw).Trim() -ne 'Synthetic release acceptance data') {
        throw 'Installer failed to preserve existing data'
    }
}
foreach ($relative in @('Chad\Chad.exe', 'Chad-Brain\Chad-Brain.exe',
    'prompts\persona\identity.md', 'src\tools\base_tool.py', 'Chad\_internal\config\hardware_models.json', 'LICENSE')) {
    if (-not (Test-Path -LiteralPath (Join-Path $TestRoot $relative))) { throw "Missing runtime asset: $relative" }
}
$helperRoot = Join-Path $TestRoot 'Chad\_internal'
foreach ($relative in @('.tools\llmfit\llmfit.exe', '.tools\llmfit\llmfit-MIT.txt', 'config\llmfit.json', 'config\ollama_models.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $helperRoot $relative))) { throw "Missing assessment asset: $relative" }
}
$expectedHelper = (Get-Content -LiteralPath (Join-Path $helperRoot 'config\llmfit.json') -Raw | ConvertFrom-Json).version
$actualHelper = & (Join-Path $helperRoot '.tools\llmfit\llmfit.exe') --version
if ($LASTEXITCODE -ne 0 -or $actualHelper.Trim() -ne "llmfit $expectedHelper") { throw 'Bundled llmfit failed its version smoke test' }
foreach ($relative in @('config\secrets.yaml', 'config\token.json', 'config\credentials.json', 'config\persona')) {
    if (Test-Path -LiteralPath (Join-Path $TestRoot $relative)) { throw "Unexpected private build input: $relative" }
}
$installedVersion = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Chad').DisplayVersion
if ($installedVersion -ne $Version) { throw "Wrong installed version: $installedVersion" }
@{ version = $Version; installer_sha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash;
    install_and_reinstall = 'passed'; retained_synthetic_data = $true; runtime_assets = 'passed';
    private_input_check = 'passed'; bundled_assessment_helper = 'passed'; note = 'Packaging/helper smoke only; a short manual startup/connection/restart visual check remains.'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path (Split-Path $Installer) 'installer-smoke.json')
