<#
.SYNOPSIS
    Open the Chad GUI connected to the always-on PRODUCTION Brain (port 8767).

.DESCRIPTION
    This does NOT start a server. The production Brain runs 24/7 under the
    supervisor (installed via install_prod.ps1). This just launches the desktop
    GUI (Limb) pointed at it. If the Brain is briefly down (e.g. mid-update),
    the GUI shows "reconnecting" and recovers automatically.

    This is the shortcut you use daily. Double-click "Chad" on your desktop.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$env:CHAD_MODE       = "cloud"
$env:CHAD_SERVER_URL = "ws://localhost:8767/ws"
$env:PYTHONPATH      = "."
$env:CHAD_UI_DEV     = "false"   # load built dist/, not the Vite dev server

& (Join-Path $RepoRoot ".venv\Scripts\python.exe") main_gui.py
