<#
.SYNOPSIS
    Ensure the Chad Dev checkout exists and shares main's secrets + local DB.

.DESCRIPTION
    Prod Chad stays in the main repo (master). Chad Dev runs from a sibling
    AIAssistant-dev folder (linked worktree or standalone clone) so branch
    switches never touch prod's checkout. Shares .venv, node_modules, .env,
    secrets, personal knowledge, and points at main's local_chad.db so Drive /
    activity / memory indexes match prod while code can diverge.

.OUTPUTS
    Writes the dev worktree path to stdout (for scripts that capture this).
#>

param(
    [string]$Branch = "dev",
    # Reuse the main profile in any existing branch checkout, not only the sibling.
    [string]$DevRoot
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_chad_paths.ps1")
$SelfRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$MainRoot = if ($env:CHAD_MAIN_ROOT -and (Test-Path $env:CHAD_MAIN_ROOT)) {
    (Resolve-Path $env:CHAD_MAIN_ROOT).Path
} else {
    Get-ChadMainRepoRoot -FromPath $SelfRoot
}
if (-not $DevRoot) { $DevRoot = Get-ChadDevRepoRoot -MainRoot $MainRoot }
$DevRoot = [IO.Path]::GetFullPath($DevRoot)
if ($DevRoot.TrimEnd('\') -eq $MainRoot.TrimEnd('\')) {
    throw "The dev checkout must be separate from the main checkout."
}

function Test-SameFileId($PathA, $PathB) {
    try {
        $a = (fsutil file queryfileid $PathA 2>$null)
        $b = (fsutil file queryfileid $PathB 2>$null)
        return ($a -and $b -and ($a -eq $b))
    } catch {
        return $false
    }
}

function Ensure-Junction($LinkPath, $TargetPath) {
    if (Test-Path $LinkPath) { return }
    if (-not (Test-Path $TargetPath)) {
        Write-Warning "Cannot link $LinkPath -> $TargetPath (target missing)"
        return
    }
    $parent = Split-Path $LinkPath -Parent
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    cmd /c mklink /J "$LinkPath" "$TargetPath" | Out-Null
}

function Ensure-FileLink($LinkPath, $TargetPath, [switch]$ForceReplace) {
    if (-not (Test-Path $TargetPath)) { return }
    if (Test-Path $LinkPath) {
        if (Test-SameFileId $LinkPath $TargetPath) {
            Write-Host "OK shared file: $LinkPath" -ForegroundColor DarkGray
            return
        }
        if (-not $ForceReplace) {
            Write-Host "OK exists (not replaced): $LinkPath" -ForegroundColor DarkGray
            return
        }
        $bak = "$LinkPath.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Move-Item -LiteralPath $LinkPath -Destination $bak -Force
        Write-Host "Backed up divergent file -> $bak" -ForegroundColor DarkGray
    }
    $parent = Split-Path $LinkPath -Parent
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    try {
        New-Item -ItemType HardLink -Path $LinkPath -Target $TargetPath -Force | Out-Null
    } catch {
        cmd /c mklink /H "$LinkPath" "$TargetPath" 2>$null | Out-Null
    }
    Write-Host "Hardlinked: $LinkPath" -ForegroundColor DarkGray
}

function Clear-StaleLocalDb($DevDbPath, $MainDbPath) {
    # Do NOT hardlink SQLite: WAL/SHM live next to the open path, so two paths
    # to the same inode can corrupt the DB. launch_dev sets CHAD_LOCAL_DB_PATH
    # to $MainDbPath instead. Remove a divergent empty/stale copy so a missed
    # env override cannot silently use the wrong DB.
    if (-not (Test-Path -LiteralPath $DevDbPath)) { return }
    if (Test-SameFileId $DevDbPath $MainDbPath) {
        # Same inode via accidental hardlink — leave it, but env path is still preferred
        Write-Host "OK local DB already same inode as main (prefer CHAD_LOCAL_DB_PATH)" -ForegroundColor DarkGray
        return
    }
    $bak = "$DevDbPath.bak_local_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Move-Item -LiteralPath $DevDbPath -Destination $bak -Force
    foreach ($suffix in @("-wal", "-shm")) {
        $side = "$DevDbPath$suffix"
        if (Test-Path -LiteralPath $side) {
            Move-Item -LiteralPath $side -Destination "$bak$suffix" -Force
        }
    }
    Write-Host "Moved non-shared local DB aside -> $bak (dev uses main via CHAD_LOCAL_DB_PATH)" -ForegroundColor DarkGray
}

function Ensure-EnvLocalDbPath($EnvPath, $DbPath) {
    # Persist absolute path in the shared .env so any launch from either checkout
    # hits the same SQLite file (and the same WAL next to it).
    if (-not (Test-Path -LiteralPath $EnvPath)) { return }
    if (-not (Test-Path -LiteralPath $DbPath)) {
        Write-Warning "Main DB missing; not writing CHAD_LOCAL_DB_PATH: $DbPath"
        return
    }
    $lines = @(Get-Content -LiteralPath $EnvPath)
    $desired = "CHAD_LOCAL_DB_PATH=$DbPath"
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match '^\s*CHAD_LOCAL_DB_PATH\s*=') {
            $found = $true
            if ($line.Trim() -eq $desired) {
                Write-Host "OK CHAD_LOCAL_DB_PATH already set in .env" -ForegroundColor DarkGray
                return
            }
            $desired
        } else {
            $line
        }
    }
    if (-not $found) {
        $out = @($out) + @("", "# Local SQLite - absolute so AIAssistant-dev shares prod index", $desired)
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines($EnvPath, [string[]]$out, $utf8)
    Write-Host "Set CHAD_LOCAL_DB_PATH in .env -> $DbPath" -ForegroundColor DarkGray
}

Set-Location $MainRoot

$devNorm = ($DevRoot -replace '\\', '/').TrimEnd('/')
$worktreeList = git worktree list --porcelain 2>$null
$hasDev = ($worktreeList -replace '\\', '/') -match [regex]::Escape($devNorm)
$devIsGit = Test-Path (Join-Path $DevRoot ".git")

if (-not (Test-Path $DevRoot)) {
    Write-Host "Creating dev worktree at $DevRoot (tracking origin/$Branch) ..." -ForegroundColor Cyan
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    git fetch origin $Branch 2>&1 | Out-Null
    $ErrorActionPreference = $prevEap
    git worktree add -B chad-dev $DevRoot origin/$Branch
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} elseif ($hasDev) {
    Write-Host "Dev worktree ready: $DevRoot" -ForegroundColor DarkGray
} elseif ($devIsGit) {
    # Standalone sibling clone (not a linked worktree) — share secrets/data only
    Write-Host "Dev checkout ready (standalone clone): $DevRoot" -ForegroundColor DarkGray
} else {
    Write-Host "[--] $DevRoot exists but is not a git checkout - remove it manually or pick another path." -ForegroundColor Yellow
    exit 1
}

# Share heavy dirs from main (gitignored in worktree)
Ensure-Junction (Join-Path $DevRoot ".venv") (Join-Path $MainRoot ".venv")
Ensure-Junction (Join-Path $DevRoot "src\clients\web\node_modules") (Join-Path $MainRoot "src\clients\web\node_modules")

# Local secrets / knowledge (gitignored - shared with main). ForceReplace so an
# old divergent .env copy from a prior setup cannot keep credentials out of sync.
Ensure-FileLink (Join-Path $DevRoot ".env") (Join-Path $MainRoot ".env") -ForceReplace
Ensure-FileLink (Join-Path $DevRoot "config\secrets.yaml") (Join-Path $MainRoot "config\secrets.yaml") -ForceReplace
Ensure-FileLink (Join-Path $DevRoot "config\preferences.yaml") (Join-Path $MainRoot "config\preferences.yaml") -ForceReplace
Ensure-FileLink (Join-Path $DevRoot "config\personal_knowledge.md") (Join-Path $MainRoot "config\personal_knowledge.md") -ForceReplace
Ensure-FileLink (Join-Path $DevRoot "data\knowledge_graph.json") (Join-Path $MainRoot "data\knowledge_graph.json") -ForceReplace

# Google OAuth token + client config. These were the one set of credentials NOT shared,
# and the divergence was silent and expensive: dev kept its own token.json, so
# re-authenticating from one checkout left the other holding a months-old refresh token.
# Google answers `invalid_grant: Token has been expired or revoked` for that, which surfaces
# as "Google auth expired AGAIN" a day after re-authenticating — because the checkout that
# was actually running never got the new token. (Found 2026-08-09: main's token was hours
# old and valid, dev's was 8 days old and dead.)
#
# ForceReplace on purpose: an existing divergent copy is precisely the bug, so it must be
# replaced rather than preserved. Safe to hardlink because the token is rewritten in place
# with open(path, 'w') (src/tools/workspace/manager.py) — a truncate keeps the inode, so
# both paths see the refresh. If that write is ever changed to write-temp-then-rename, the
# rename breaks the link and this must become a shared path via env var instead (see
# CHAD_LOCAL_DB_PATH for that pattern).
Ensure-FileLink (Join-Path $DevRoot "config\token.json") (Join-Path $MainRoot "config\token.json") -ForceReplace
Ensure-FileLink (Join-Path $DevRoot "config\token_node.json") (Join-Path $MainRoot "config\token_node.json") -ForceReplace
Ensure-FileLink (Join-Path $DevRoot "config\credentials.json") (Join-Path $MainRoot "config\credentials.json") -ForceReplace

$mainDb = Get-ChadLocalDbPath -MainRoot $MainRoot
Clear-StaleLocalDb (Join-Path $DevRoot "data\local_chad.db") $mainDb
Ensure-EnvLocalDbPath (Join-Path $MainRoot ".env") $mainDb
# Dev .env is hardlinked to main after Ensure-FileLink; no second write needed.

# Persona overlay (#589) is COPIED, not hardlinked, unlike the secrets above.
# Those are read-only credentials, so one shared inode is what you want. The
# persona overlay is actively written - by the reflection loop, the persona
# editor, and prompt experiments - and a hardlinked write goes straight through
# to prod's inode, so a throwaway dev experiment would silently rewrite the real
# persona. Copying gives the worktree a realistic starting persona that can then
# diverge harmlessly. The generic baseline (prompts/persona) is git-tracked and
# needs no help here.
$personaSrc = Join-Path $MainRoot "config\persona"
$personaDst = Join-Path $DevRoot "config\persona"
if ((Test-Path $personaSrc) -and -not (Test-Path $personaDst)) {
    Copy-Item -Path $personaSrc -Destination $personaDst -Recurse -Force
    Write-Host "Copied persona overlay -> $personaDst (independent of prod)" -ForegroundColor DarkGray
}

$logDir = Join-Path $DevRoot "data\dev_logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

Write-Output $DevRoot
