"""Unit tests for browser_safety approval modes (issue #328)."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration import browser_safety


@pytest.mark.parametrize(
    "mode,tool_name,params,current_domain,expect_approval",
    [
        ("allow_all", "browser_click", {"element": "Submit order"}, "evil.com", False),
        ("allow_all", "browser_file_upload", {}, "evil.com", False),
        ("ask_all", "browser_snapshot", {}, "github.com", True),
        ("ask_all", "browser_navigate", {"url": "https://example.com"}, None, True),
        ("model_decide", "browser_snapshot", {}, "github.com", False),
        ("model_decide", "browser_click", {"element": "Submit order"}, "github.com", True),
        ("model_decide", "browser_click", {"element": "Read more"}, "unknown.com", True),
        ("model_decide", "browser_click", {"element": "Read more"}, "github.com", False),
    ],
)
def test_classify_approval_modes(mode, tool_name, params, current_domain, expect_approval):
    with patch.object(browser_safety.ConfigManager, "get", side_effect=lambda key, default=None: {
        "tools.browser_approval_mode": mode,
        "tools.browser_write_allowlist": ["github.com"],
    }.get(key, default)):
        verdict = browser_safety.classify(tool_name, params, current_domain=current_domain)
    assert verdict["requires_approval"] is expect_approval


def test_get_browser_approval_mode_unknown_falls_back():
    with patch.object(browser_safety.ConfigManager, "get", return_value="bogus"):
        assert browser_safety.get_browser_approval_mode() == "allow_all"
