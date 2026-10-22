# Kill orphaned Chad subprocesses (MCP prewarm, Playwright MCP, Hands daemon, etc.)
# Safe to run anytime Chad is not actively in use.
#
# Usage:
#   .\scripts\maintenance\kill_chad_zombies.ps1
#   .\scripts\maintenance\kill_chad_zombies.ps1 -WhatIf   # preview only
#
# Does NOT kill a manually opened ChadBrowser chrome window (chrome.exe with
# --user-data-dir=ChadBrowser but no @playwright/mcp parent). Does kill
# @playwright/mcp trees (including chrome children, and rogue MCP attached to
# the real Chrome profile).

param(
    [switch]$WhatIf
)

$patterns = @(
    @{ Name = "Playwright MCP"; Regex = '@playwright/mcp|playwright\\mcp\\cli\.js|playwright/mcp/cli\.js' },
    @{ Name = "MCP prewarm"; Regex = 'npx.*--help|mcp-server|mcp-fetch|todoist-mcp' },
    @{ Name = "Hands daemon"; Regex = 'hands\.py' }
)

$total = 0
$killedPids = @{}

function Stop-Tree([int]$ProcessId, [string]$Label) {
    if ($killedPids.ContainsKey($ProcessId)) { return }
    $killedPids[$ProcessId] = $true
    $script:total++
    if ($WhatIf) {
        Write-Host "[what-if] Would kill PID $ProcessId ($Label)"
        return
    }
    try {
        # taskkill /T kills the whole tree (chrome children of node)
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
        Write-Host "Killed PID $ProcessId ($Label) + children"
    } catch {
        try {
            Stop-Process -Id $ProcessId -Force -ErrorAction Stop
            Write-Host "Killed PID $ProcessId ($Label)"
        } catch {
            Write-Warning "Failed to kill PID $ProcessId : $_"
        }
    }
}

foreach ($entry in $patterns) {
    $matches = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and ($_.CommandLine -match $entry.Regex)
    }

    foreach ($proc in $matches) {
        Stop-Tree -ProcessId $proc.ProcessId -Label $entry.Name
    }
}

if ($WhatIf) {
    Write-Host "Would kill $total process tree(s)."
} else {
    Write-Host "Done. Killed $total zombie process tree(s)."
    $pwLeft = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match '@playwright/mcp') })
    Write-Host "Remaining @playwright/mcp: $($pwLeft.Count)"
    Write-Host "Remaining chrome: $((Get-Process chrome -ErrorAction SilentlyContinue).Count)"
    Write-Host "Remaining node: $((Get-Process node -ErrorAction SilentlyContinue).Count)"
}
