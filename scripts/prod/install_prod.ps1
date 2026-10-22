<#
.SYNOPSIS
    One-time setup to make Chad a real always-on production app on this PC.

.DESCRIPTION
    1. Adds a Startup-folder entry that launches the supervisor (hidden) at logon,
       so the production Brain is up 24/7 whenever you're logged in. The supervisor
       itself keeps the Brain alive and restarts it on exit.
    2. Creates desktop shortcuts: "Chad" (prod GUI app) and "Chad Dev" (dev stack).
    3. Starts the Brain now and prints recommended power settings.

    No admin required (per-user Startup folder, not a system scheduled task).
    Re-running this is safe - it replaces the existing entries.
#>

$RepoRoot      = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "_chad_paths.ps1")
$DevRoot       = Get-ChadDevRepoRoot -MainRoot $RepoRoot
$DevLaunch     = if (Test-Path (Join-Path $DevRoot "scripts\prod\launch_dev.ps1")) {
    Join-Path $DevRoot "scripts\prod\launch_dev.ps1"
} else {
    Join-Path $PSScriptRoot "launch_dev.ps1"
}
$SupervisorVbs = Join-Path $PSScriptRoot "chad_supervisor_launch.vbs"

Write-Host "=== Chad production install ===" -ForegroundColor Green
Write-Host "Repo: $RepoRoot"

# --- 1. Autostart via the per-user Startup folder (no admin needed) ---
$startup = [Environment]::GetFolderPath("Startup")
$autostart = New-Object -ComObject WScript.Shell
$autoLnk = $autostart.CreateShortcut((Join-Path $startup "ChadBrain.lnk"))
$autoLnk.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$autoLnk.Arguments = "`"$SupervisorVbs`""
$autoLnk.WorkingDirectory = $RepoRoot
$autoLnk.Description = "Always-on Chad production Brain (supervised)."
$autoLnk.Save()
Write-Host "[OK] Autostart registered (Startup folder -> supervisor at logon)." -ForegroundColor Green

# --- 2. Desktop + Start-menu shortcuts ---
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$IconPath = Join-Path $PSScriptRoot "chad.ico"
$ProdVbs  = Join-Path $PSScriptRoot "launch_prod_gui.vbs"

# Prefer the packaged binary (scripts\prod\build_exe.ps1 -> dist\Chad\Chad.exe)
# when it exists; otherwise fall back to the windowless VBS launcher. Both open
# the GUI as a real app with no console flash.
$ChadExe = Join-Path $RepoRoot "dist\Chad\Chad.exe"
$UsingExe = Test-Path $ChadExe
if ($UsingExe) {
    $ChadTarget = $ChadExe
    $ChadArgs   = ""
    $ChadIcon   = $ChadExe   # embedded icon
    Write-Host "[OK] Packaged Chad.exe found - shortcuts will point at it." -ForegroundColor Green
} else {
    $ChadTarget = "$env:SystemRoot\System32\wscript.exe"
    $ChadArgs   = "`"$ProdVbs`""
    $ChadIcon   = $IconPath
    Write-Host "[--] No packaged Chad.exe (run build_exe.ps1 to make one); using VBS launcher." -ForegroundColor DarkGray
}

function New-ChadShortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $ChadTarget
    if ($ChadArgs) { $lnk.Arguments = $ChadArgs }
    $lnk.WorkingDirectory = $RepoRoot
    $lnk.Description = "Chad - production assistant"
    if (Test-Path $ChadIcon) { $lnk.IconLocation = $ChadIcon }
    $lnk.Save()
}

# Desktop "Chad"
New-ChadShortcut (Join-Path $desktop "Chad.lnk")
Write-Host "[OK] Shortcut: Chad (app)" -ForegroundColor Green

# Start-menu "Chad" (so it shows in the Start menu / search like a real app)
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Chad"
New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
New-ChadShortcut (Join-Path $startMenu "Chad.lnk")
Write-Host "[OK] Start-menu entry: Chad" -ForegroundColor Green

# "Chad Dev" - visible console on purpose (you want to see dev logs).
$dev = $shell.CreateShortcut((Join-Path $desktop "Chad Dev.lnk"))
$dev.TargetPath = "powershell.exe"
$dev.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Normal -File `"$DevLaunch`""
$dev.WorkingDirectory = $RepoRoot
$dev.Description = "Chad - dev stack (Brain 8788 + GUI)"
if (Test-Path $IconPath) { $dev.IconLocation = $IconPath }
$dev.Save()
Write-Host "[OK] Shortcut: Chad Dev" -ForegroundColor Green

# --- 2b. Add/Remove Programs entry (per-user, no admin) ---
# Registers Chad under Settings > Apps so it looks like a real installed app and
# has a one-click uninstall that runs uninstall_prod.ps1.
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Chad"
$UninstallPs1 = Join-Path $PSScriptRoot "uninstall_prod.ps1"
$UninstallCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$UninstallPs1`""
New-Item -Path $UninstallKey -Force | Out-Null
Set-ItemProperty -Path $UninstallKey -Name "DisplayName"     -Value "Chad"
Set-ItemProperty -Path $UninstallKey -Name "DisplayIcon"     -Value $ChadIcon
Set-ItemProperty -Path $UninstallKey -Name "DisplayVersion"  -Value "1.0.0"
Set-ItemProperty -Path $UninstallKey -Name "Publisher"       -Value "Matthew Loyed"
Set-ItemProperty -Path $UninstallKey -Name "InstallLocation" -Value $RepoRoot
Set-ItemProperty -Path $UninstallKey -Name "UninstallString" -Value $UninstallCmd
Set-ItemProperty -Path $UninstallKey -Name "NoModify"        -Value 1 -Type DWord
Set-ItemProperty -Path $UninstallKey -Name "NoRepair"        -Value 1 -Type DWord
Write-Host "[OK] Add/Remove Programs entry registered (Settings > Apps > Chad)." -ForegroundColor Green

# --- 3. Start the Brain now (so you don't have to log out/in) ---
# Only start if nothing is already serving on 8767 (server.py also auto-frees the
# port, but this avoids needlessly killing a healthy running instance).
$already = $null
try { $already = Invoke-WebRequest -Uri "http://localhost:8767/health" -TimeoutSec 2 -UseBasicParsing } catch {}
if ($already -and $already.StatusCode -eq 200) {
    Write-Host "[--] A server is already running on 8767; leaving it. (Use the Update button or restart to load new code.)" -ForegroundColor DarkGray
} else {
    & "$env:SystemRoot\System32\wscript.exe" "$SupervisorVbs"
    Write-Host "[OK] Production Brain starting (supervised)." -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Recommended power settings (keep Brain alive, save the monitor) ===" -ForegroundColor Yellow
Write-Host "  powercfg /change standby-timeout-ac 0     # never sleep on AC"
Write-Host "  powercfg /change monitor-timeout-ac 10    # display off after 10 min"
Write-Host ""
Write-Host "Done. Open 'Chad' on your desktop to connect. To remove: uninstall_prod.ps1" -ForegroundColor Green
