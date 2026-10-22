"""CI gate: tracked config must not contain machine-specific personal state (#408)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKED_CONFIG_FILES = (
    REPO_ROOT / "config" / "config.yaml",
    REPO_ROOT / "config" / "profiles.yaml",
)

FORBIDDEN_PATTERNS = (
    (r"C:\\Users\\[^\\%]+", "Windows user profile path"),
    (r"XboxGames", "XboxGames install path"),
    (r"https://[a-z0-9]+\.supabase\.co", "Supabase project URL"),
    (r"hubspace_control", "Hubspace fast-track shortcut"),
    (r"onrender\.com", "Render deployment URL"),
)


@pytest.mark.parametrize("config_path", TRACKED_CONFIG_FILES, ids=lambda p: p.name)
def test_tracked_config_has_no_personal_state(config_path: Path):
    assert config_path.exists(), f"Missing tracked config: {config_path}"
    text = config_path.read_text(encoding="utf-8")
    violations = []
    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            violations.append(label)
    assert not violations, f"{config_path.name} contains personal state: {', '.join(violations)}"
