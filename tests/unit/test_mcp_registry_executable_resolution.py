"""Unit tests for _resolve_executable (src/core/tools/mcp_registry.py).

Regression guard for the Windows WinError-2 bug: asyncio.create_subprocess_exec('npx', ...)
fails on Windows because it doesn't append PATHEXT (npx is really npx.CMD), which silently
killed every npx-based stdio MCP (github, brave-search, google-drive, fetch, ...)."""
from __future__ import annotations

import os

import pytest

from src.core.tools import mcp_registry
from src.core.tools.mcp_registry import _resolve_executable


def test_expands_env_vars():
    # $VAR (not %VAR%) -- os.path.expandvars only expands %VAR% on Windows (ntpath); the
    # posix implementation CI runs on only understands $VAR/${VAR}. $VAR works on both.
    os.environ["_MCP_TEST_VAR"] = "somevalue"
    assert _resolve_executable("$_MCP_TEST_VAR") == "somevalue"


def test_command_with_extension_is_untouched(monkeypatch):
    # Even on Windows, a command that already carries an extension shouldn't be re-resolved.
    monkeypatch.setattr(os, "name", "nt")
    assert _resolve_executable("npx.cmd") == "npx.cmd"


def test_command_with_path_is_untouched(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    p = r"C:\Program Files\nodejs\npx.CMD"
    assert _resolve_executable(p) == p


def test_bare_command_resolved_via_which_on_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(mcp_registry.shutil, "which", lambda c: r"C:\Program Files\nodejs\npx.CMD")
    assert _resolve_executable("npx") == r"C:\Program Files\nodejs\npx.CMD"


def test_bare_command_falls_back_when_which_fails(monkeypatch):
    # If which can't find it, return the original so the real spawn error still surfaces.
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(mcp_registry.shutil, "which", lambda c: None)
    assert _resolve_executable("npx") == "npx"


def test_no_op_on_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    # which should not even be consulted on posix — bare name returned as-is.
    monkeypatch.setattr(mcp_registry.shutil, "which",
                        lambda c: pytest.fail("which should not be called on posix"))
    assert _resolve_executable("npx") == "npx"
