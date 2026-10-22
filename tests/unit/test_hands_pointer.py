"""
Unit tests for the Hands Daemon pointer coordinate-safety helper (#276).

`HandsDaemon._resolve_point` is pure coordinate math (clamp to the virtual
desktop + reject garbage input) and can be tested without touching real mouse
hardware. We monkeypatch the win32api metrics so the "screen" is deterministic.
"""

import sys
import types

import pytest

from src.daemons.hands import HandsDaemon


def _fake_win32api(min_x, min_y, width, height):
    """Build a stand-in win32api module reporting a fixed virtual-screen rect."""
    metrics = {
        76: min_x,   # SM_XVIRTUALSCREEN
        77: min_y,   # SM_YVIRTUALSCREEN
        78: width,   # SM_CXVIRTUALSCREEN
        79: height,  # SM_CYVIRTUALSCREEN
    }
    mod = types.ModuleType("win32api")
    mod.GetSystemMetrics = lambda idx: metrics[idx]
    return mod


def _fake_win32con():
    mod = types.ModuleType("win32con")
    mod.SM_XVIRTUALSCREEN = 76
    mod.SM_YVIRTUALSCREEN = 77
    mod.SM_CXVIRTUALSCREEN = 78
    mod.SM_CYVIRTUALSCREEN = 79
    return mod


@pytest.fixture
def daemon(monkeypatch):
    monkeypatch.setitem(sys.modules, "win32api", _fake_win32api(0, 0, 1920, 1080))
    monkeypatch.setitem(sys.modules, "win32con", _fake_win32con())
    # Avoid the daemon actually opening sockets / threads at construction time.
    return HandsDaemon.__new__(HandsDaemon)


def test_in_bounds_point_is_unchanged(daemon):
    assert daemon._resolve_point(100, 200) == (100, 200)


def test_point_is_clamped_to_screen_bounds(daemon):
    # Way off the right/bottom edge -> clamped to max (width-1, height-1).
    assert daemon._resolve_point(5000, 5000) == (1919, 1079)
    # Negative -> clamped to the virtual origin.
    assert daemon._resolve_point(-50, -50) == (0, 0)


def test_float_and_string_coords_are_coerced(daemon):
    assert daemon._resolve_point(10.6, "20") == (11, 20)


def test_non_numeric_coords_rejected(daemon):
    with pytest.raises(ValueError):
        daemon._resolve_point("left", None)


def test_multimonitor_negative_origin(monkeypatch):
    # Second monitor to the left of primary: virtual origin is negative.
    monkeypatch.setitem(sys.modules, "win32api", _fake_win32api(-1920, 0, 3840, 1080))
    monkeypatch.setitem(sys.modules, "win32con", _fake_win32con())
    d = HandsDaemon.__new__(HandsDaemon)
    assert d._resolve_point(-1900, 500) == (-1900, 500)   # valid on left monitor
    assert d._resolve_point(-5000, 500) == (-1920, 500)   # clamped to left edge
