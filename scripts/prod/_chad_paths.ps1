function Get-ChadMainRepoRoot {
    param([string]$FromPath)

    if ($env:CHAD_MAIN_ROOT -and (Test-Path -LiteralPath $env:CHAD_MAIN_ROOT)) {
        return (Resolve-Path -LiteralPath $env:CHAD_MAIN_ROOT).Path
    }

    # Sibling checkout: .../AIAssistant-dev shares data with .../AIAssistant
    $leaf = Split-Path -Leaf $FromPath
    if ($leaf -eq "AIAssistant-dev") {
        $sibling = Join-Path (Split-Path $FromPath -Parent) "AIAssistant"
        if (Test-Path -LiteralPath $sibling) {
            return (Resolve-Path -LiteralPath $sibling).Path
        }
    }

    $line = git -C $FromPath worktree list 2>$null | Select-Object -First 1
    if ($line -match '^(\S+)') {
        return ($Matches[1] -replace '/', [IO.Path]::DirectorySeparatorChar)
    }
    return $FromPath
}

function Get-ChadDevRepoRoot {
    param([string]$MainRoot)
    Join-Path (Split-Path $MainRoot -Parent) "AIAssistant-dev"
}

function Get-ChadLocalDbPath {
    param([string]$MainRoot)
    Join-Path $MainRoot "data\local_chad.db"
}
