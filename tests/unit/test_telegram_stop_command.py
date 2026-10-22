"""Unit tests for Telegram /stop control lane."""

from __future__ import annotations

import pytest

from src.remote.telegram.control import is_telegram_stop_command


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/stop", True),
        ("/STOP", True),
        ("/stop@CHAD_AiAssistant_bot", True),
        ("/cancel", True),
        ("/cancel@foo_bot more", True),
        ("stop", True),
        ("cancel", True),
        ("Stop", True),
        ("/status", False),
        ("please stop later", False),
        ("check my email", False),
        ("", False),
        (None, False),
    ],
)
def test_is_telegram_stop_command(text, expected):
    assert is_telegram_stop_command(text) is expected
