"""
Unit tests for the Hands Daemon window-move primitive (move_window).

Covers the `MOVE_WINDOW` branch of `_execute_primitive` (mocked pygetwindow, so
no real window is touched). Explicit x/y only — monitor-targeted moves were
removed in favor of windows-semantic-ui's window_management(move_to_monitor),
which pairs the move with a list_monitors discovery call. The old monitor_index
guessing here had no discovery step and silently disagreed with that server's
own (different) monitor numbering, sending windows to the wrong screen.
"""

import sys
import types

import pytest

from src.daemons.hands import HandsDaemon
from src.daemons.protocol import DaemonAction


class _FakeWindow:
    def __init__(self, title="Notepad"):
        self.title = title
        self.isMinimized = False
        self.moved_to = None

    def restore(self):
        self.isMinimized = False

    def moveTo(self, x, y):
        self.moved_to = (x, y)


def _fake_pygetwindow(window):
    mod = types.ModuleType("pygetwindow")
    mod.getWindowsWithTitle = lambda title: [window]
    return mod


@pytest.fixture
def daemon():
    return HandsDaemon.__new__(HandsDaemon)


def test_move_window_explicit_xy(monkeypatch, daemon):
    win = _FakeWindow()
    monkeypatch.setitem(sys.modules, "pygetwindow", _fake_pygetwindow(win))

    daemon._execute_primitive(
        DaemonAction.MOVE_WINDOW, {"window_title": "Notepad", "x": 100, "y": 200}
    )

    assert win.moved_to == (100, 200)


def test_move_window_missing_xy_raises(monkeypatch, daemon):
    win = _FakeWindow()
    monkeypatch.setitem(sys.modules, "pygetwindow", _fake_pygetwindow(win))

    with pytest.raises(ValueError, match="requires explicit x/y"):
        daemon._execute_primitive(DaemonAction.MOVE_WINDOW, {"window_title": "Notepad"})


def test_move_window_not_found_raises(monkeypatch, daemon):
    mod = types.ModuleType("pygetwindow")
    mod.getWindowsWithTitle = lambda title: []
    monkeypatch.setitem(sys.modules, "pygetwindow", mod)

    with pytest.raises(RuntimeError, match="not found"):
        daemon._execute_primitive(
            DaemonAction.MOVE_WINDOW, {"window_title": "Nonexistent", "x": 0, "y": 0}
        )


def test_move_window_restores_minimized_window(monkeypatch, daemon):
    win = _FakeWindow()
    win.isMinimized = True
    monkeypatch.setitem(sys.modules, "pygetwindow", _fake_pygetwindow(win))

    daemon._execute_primitive(
        DaemonAction.MOVE_WINDOW, {"window_title": "Notepad", "x": 50, "y": 50}
    )

    assert win.isMinimized is False
    assert win.moved_to == (50, 50)
