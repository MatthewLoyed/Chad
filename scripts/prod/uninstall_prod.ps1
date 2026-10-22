<#
.SYNOPSIS
    Stop the always-on Chad production Brain and remove its autostart.

.DESCRIPTION
    Signals the supervisor to stop gracefully, removes the Startup-folder autostart,
    and removes the desktop shortcuts. Does not touch code or data.
#>

$ErrorActionPreference = "Continue"
$StopFlag = Join-Path $PSScriptRoot ".stop"

# 1. Ask the supervisor to exit after the current iteration
New-Item -ItemType File -Path $StopFlag -Force | Out-Null
Write-Host "[..] Stop flag set - supervisor will exit shortly." -ForegroundColor Yellow

# 2. Remove the Startup-folder autostart entry
$startup = [Environment]::GetFolderPath("Startup")
$autoLnk = Join-Path $startup "ChadBrain.lnk"
if (Test-Path $autoLnk) { Remove-Item $autoLnk -Force; Write-Host "[OK] Autostart entry removed." -ForegroundColor Green }
else { Write-Host "[--] No autostart entry found." -ForegroundColor DarkGray }

# 3. Remove desktop shortcuts
$desktop = [Environment]::GetFolderPath("Desktop")
foreach ($name in @("Chad", "Chad Dev")) {
    $lnk = Join-Path $desktop "$name.lnk"
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "[OK] Removed shortcut: $name" -ForegroundColor Green }
}

# 3b. Remove Start-menu folder
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Chad"
if (Test-Path $startMenu) { Remove-Item $startMenu -Recurse -Force; Write-Host "[OK] Removed Start-menu entry." -ForegroundColor Green }

# 3c. Remove Add/Remove Programs entry
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Chad"
if (Test-Path $UninstallKey) { Remove-Item $UninstallKey -Recurse -Force; Write-Host "[OK] Removed Add/Remove Programs entry." -ForegroundColor Green }

# 4. Best-effort: free port 8767 so the Brain actually stops now
try {
    $conns = Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
} catch {}

Write-Host "Done. Production Chad stopped and autostart removed." -ForegroundColor Green
