<#
.SYNOPSIS
    Assemble a self-contained installer payload from dist\Chad, dist\Chad-Brain,
    and the tracked config templates - everything the NSIS installer (#429)
    needs to lay down a working CHAD_HOME on a machine with no source checkout.

.DESCRIPTION
    scripts\prod\build_exe.ps1 produces dist\Chad\ and dist\Chad-Brain\, but
    each one's chad_home.txt still points at the *build machine's* repo path
    (config\, data\, etc. are assumed to live there). That's fine for the
    maintainer's own dev checkout, but breaks completely on a stranger's
    machine. This script copies the tracked, non-secret config surface next
    to the frozen apps so the installer can point CHAD_HOME at the install
    directory itself instead.

    Deliberately excludes secrets.yaml / preferences.yaml / personal_knowledge.md
    (gitignored, user-specific) - only the .example templates ship, and the
    first-run setup wizard (#428) / bootstrap_config_if_needed() instantiate
    the real files on first launch.

    Output: <repo>\dist\installer_payload\
      Chad\               (copy of dist\Chad, chad_home.txt stripped)
      Chad-Brain\          (copy of dist\Chad-Brain, chad_home.txt stripped)
      config\               (tracked config/*.yaml, *.py, *.json, *.jinja + *.example)
      .env.example
      CLAUDE.md.example

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prod\stage_installer_payload.ps1
#>
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DistRoot = Join-Path $RepoRoot "dist"
$PayloadDir = Join-Path $DistRoot "installer_payload"

Write-Host "=== Stage installer payload ===" -ForegroundColor Green

foreach ($appName in @("Chad", "Chad-Brain")) {
    $src = Join-Path $DistRoot $appName
    if (-not (Test-Path $src)) {
        throw "$src not found - run scripts\prod\build_exe.ps1 -All first."
    }
}

if (Test-Path $PayloadDir) {
    $ResolvedPayload = (Resolve-Path -LiteralPath $PayloadDir).Path
    $ResolvedDist = (Resolve-Path -LiteralPath $DistRoot).Path
    if ($ResolvedPayload -ne (Join-Path $ResolvedDist "installer_payload")) {
        throw "Refusing to replace an unexpected payload path: $ResolvedPayload"
    }
    Remove-Item -LiteralPath $ResolvedPayload -Recurse -Force
}
New-Item -ItemType Directory -Path $PayloadDir | Out-Null

foreach ($appName in @("Chad", "Chad-Brain")) {
    $src = Join-Path $DistRoot $appName
    $dest = Join-Path $PayloadDir $appName
    Write-Host "[..] Copying $appName ..." -ForegroundColor Cyan
    Copy-Item -Path $src -Destination $dest -Recurse -Force
    # chad_home.txt currently points at this build machine's repo - the
    # NSIS installer writes a fresh one pointing at the actual install dir.
    $staleHome = Join-Path $dest "chad_home.txt"
    if (Test-Path $staleHome) { Remove-Item $staleHome -Force }
}

# PyTorch also needs the C++ standard library at the bundle DLL search root.
# The sklearn wheel already redistributes this exact runtime, but its private
# .libs directory is not searched when torch loads c10.dll on a clean machine.
$BrainInternal = Join-Path $PayloadDir "Chad-Brain\_internal"
$CppRuntime = Join-Path $BrainInternal "msvcp140.dll"
if ((Test-Path (Join-Path $BrainInternal "torch\lib\c10.dll")) -and -not (Test-Path $CppRuntime)) {
    $BundledRuntime = Join-Path $BrainInternal "sklearn\.libs\msvcp140.dll"
    if (-not (Test-Path $BundledRuntime)) {
        throw "The Brain is missing its bundled MSVC C++ runtime. Rebuild with the sklearn wheel runtime included."
    }
    Copy-Item -LiteralPath $BundledRuntime -Destination $CppRuntime
}

Write-Host "[..] Copying config templates ..." -ForegroundColor Cyan
$ConfigDest = Join-Path $PayloadDir "config"
New-Item -ItemType Directory -Path $ConfigDest | Out-Null

$ConfigFiles = @(
    "chat_template_qwen36_35b.jinja",
    "config.yaml",
    "heartbeat_config.json",
    "model_capabilities.yaml",
    "model_profiles.py",
    "model_registry.py",
    "profiles.yaml",
    "provider_profiles.yaml",
    "tool_policies.yaml",
    "personal_knowledge.md.example",
    "preferences.yaml.example",
    "secrets.yaml.example"
)
foreach ($f in $ConfigFiles) {
    $srcFile = Join-Path $RepoRoot "config\$f"
    if (-not (Test-Path $srcFile)) { throw "Expected tracked config file missing: $srcFile" }
    Copy-Item -Path $srcFile -Destination (Join-Path $ConfigDest $f) -Force
}

Copy-Item -Path (Join-Path $RepoRoot ".env.example") -Destination (Join-Path $PayloadDir ".env.example") -Force
Copy-Item -Path (Join-Path $RepoRoot "CLAUDE.md.example") -Destination (Join-Path $PayloadDir "CLAUDE.md.example") -Force

# Persona fragments and skill instructions are runtime files, not Python modules.
# PyInstaller's hiddenimports do not include them. Stage only Git-tracked files,
# never the personal config/persona overlay or untracked local skill material.
$RuntimeFiles = @(git -C $RepoRoot ls-files -- prompts src/skills src/tools scripts/setup/setup_lmstudio.py)
if ($LASTEXITCODE -ne 0) { throw "Could not enumerate tracked runtime assets" }
if (-not ($RuntimeFiles -contains "prompts/persona/identity.md")) {
    throw "Tracked baseline persona is missing"
}
foreach ($relative in $RuntimeFiles) {
    $destination = Join-Path $PayloadDir $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
    Copy-Item -LiteralPath (Join-Path $RepoRoot $relative) -Destination $destination -Force
}
Copy-Item -LiteralPath (Join-Path $RepoRoot "LICENSE") -Destination (Join-Path $PayloadDir "LICENSE") -Force

Write-Host "[OK] Payload staged at $PayloadDir" -ForegroundColor Green
