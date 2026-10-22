#Requires -Version 5.1
<#
.SYNOPSIS
  Migrate personal config from tracked files into preferences.yaml (one-time).

.DESCRIPTION
  Before the #408 config generalization, personal apps/routines/monitors lived in
  tracked profiles.yaml and some config.yaml defaults. This script deep-merges those
  personal bits into config/preferences.yaml under `profile:` and other keys.

  Safe to re-run — only adds missing keys; does not remove existing preferences.

  Run from your prod repo root (master):
    .\scripts\setup\migrate_personal_config.ps1
#>
$ErrorActionPreference = "Stop"

$repoRoot = (Get-Location).Path
$configDir = Join-Path $repoRoot "config"
$prefsPath = Join-Path $configDir "preferences.yaml"

if (-not (Test-Path $prefsPath)) {
    $example = Join-Path $configDir "preferences.yaml.example"
    if (Test-Path $example) {
        Copy-Item $example $prefsPath
        Write-Host "[copy] Created preferences.yaml from example"
    } else {
        New-Item -ItemType File -Path $prefsPath -Force | Out-Null
        Write-Host "[create] Empty preferences.yaml"
    }
}

# Use Python for reliable YAML merge (PyYAML in venv).
$py = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "python"
}

$mergeScript = @'
import copy
import sys
from pathlib import Path

import yaml

def deep_merge(base, update):
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)

repo = Path(sys.argv[1])
prefs_path = repo / "config" / "preferences.yaml"
prefs = yaml.safe_load(prefs_path.read_text(encoding="utf-8")) or {}

# Personal overlay migrated from pre-#408 tracked defaults.
personal = {
    "profile": {
        "monitors": {"layout": ["center", "left", "right", "laptop"]},
        "routine_builder_model": "qwen/qwen3.5-9b",
        "apps": {
            "outlook": {
                "path": r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",
                "process": "outlook.exe",
                "titles": ["Outlook"],
            },
            "minecraft": {
                "path": r"C:\XboxGames\Minecraft Launcher\Content\Minecraft.exe",
                "process": "javaw.exe",
                "titles": ["Minecraft"],
            },
            "rider": {
                "path": r"%USERPROFILE%\AppData\Local\Programs\Rider\bin\rider64.exe",
                "process": "rider64.exe",
                "titles": ["JetBrains Rider", "Rider"],
            },
            "cursor": {
                "path": r"%USERPROFILE%\AppData\Local\Programs\cursor\Cursor.exe",
                "process": "Cursor.exe",
                "titles": ["Cursor"],
            },
        },
        "routines": {
            "coding": [
                {"action": "launch_app", "app": "cursor", "monitor": 1},
                {"action": "launch_app", "app": "chrome", "monitor": 2},
                {"action": "launch_app", "app": "outlook", "monitor": 4},
            ],
            "gaming": [
                {"action": "launch_app", "app": "minecraft", "monitor": 1},
                {"action": "set_volume", "level": 70},
            ],
        },
        "workspace": {
            "coding_roots": [r"%USERPROFILE%\Documents\Coding"],
        },
        "file_opener": {
            "search_paths": [
                r"%USERPROFILE%\Videos",
            ],
        },
    },
    "tools": {
        "fast_track_commands": [
            {
                "alias": "lamp_on",
                "label": "Lamp On",
                "tool": "hubspace_control",
                "params": {"action": "turn_on", "device_name": "lamp"},
            },
            {
                "alias": "lamp_off",
                "label": "Lamp Off",
                "tool": "hubspace_control",
                "params": {"action": "turn_off", "device_name": "lamp"},
            },
        ],
    },
    "voice_mode": {"enabled": True},
    "local_models": {
        "lifecycle": {"vram_budget_gb": 16},
    },
    "reflection": {"notify_telegram": True},
}

deep_merge(prefs, personal)
prefs_path.write_text(yaml.safe_dump(prefs, default_flow_style=False, sort_keys=False), encoding="utf-8")
print(f"Updated {prefs_path}")
'@

$tmpPy = Join-Path $env:TEMP "chad_migrate_personal_config.py"
Set-Content -Path $tmpPy -Value $mergeScript -Encoding UTF8
& $py $tmpPy $repoRoot
Remove-Item $tmpPy -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Done. Personal overrides are now in config/preferences.yaml (gitignored)."
Write-Host "Pull the #408 branch to get generalized tracked config.yaml / profiles.yaml."
