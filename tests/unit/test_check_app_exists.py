from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from src.daemons import app_lookup
from src.daemons.hands import HandsDaemon
from src.daemons.protocol import DaemonAction
from src.tools.os.check_app_exists import CheckAppExistsTool


@pytest.fixture
def safe_lookup(monkeypatch, tmp_path):
    import subprocess, webbrowser, os
    def forbid(*args, **kwargs): raise AssertionError("Read-only lookup attempted an execution operation")
    monkeypatch.setattr(subprocess, "Popen", forbid)
    monkeypatch.setattr(subprocess, "run", forbid)
    monkeypatch.setattr(webbrowser, "open", forbid)
    monkeypatch.setattr(os, "startfile", forbid, raising=False)
    monkeypatch.setattr(app_lookup.shutil, "which", lambda name: None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "user"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "machine"))
    return tmp_path


def test_daemon_uses_read_only_lookup_without_launching(safe_lookup):
    exe = safe_lookup / "App.exe"; exe.write_bytes(b"fixture")
    daemon = HandsDaemon.__new__(HandsDaemon)
    result = daemon._execute_primitive(DaemonAction.CHECK_APP_EXISTS, {"name": "Fixture App", "explicit_path": str(exe)})
    assert result["found"] and result["executable_exists"]
    assert result["launch_tested"] is False
    assert result["method"] == "config"


def test_path_lookup_is_read_only(safe_lookup, monkeypatch):
    exe = safe_lookup / "app.exe"; exe.write_bytes(b"fixture")
    monkeypatch.setattr(app_lookup.shutil, "which", lambda name: str(exe) if name == "app.exe" else None)
    assert app_lookup.check_application("app")["method"] == "path"


def test_start_menu_shortcut_target_is_verified(safe_lookup, monkeypatch):
    folder = safe_lookup / "user/Microsoft/Windows/Start Menu/Programs"
    folder.mkdir(parents=True)
    (folder / "Fixture App.lnk").write_bytes(b"shortcut fixture")
    exe = safe_lookup / "App.exe"; exe.write_bytes(b"fixture")
    monkeypatch.setattr(app_lookup, "_read_shortcut_target", lambda path: str(exe))
    result = app_lookup.check_application("Fixture App")
    assert result["method"] == "start_menu"
    assert result["found"] and result["resolved_target"] == str(exe)
    exe.unlink()
    result = app_lookup.check_application("Fixture App")
    assert result["found"] is False
    assert result["executable_exists"] is False


def test_shortcut_only_is_not_executable_verification(safe_lookup, monkeypatch):
    shortcut = safe_lookup / "App.lnk"; shortcut.write_bytes(b"fixture")
    monkeypatch.setattr(app_lookup, "_read_shortcut_target", lambda path: None)
    result = app_lookup.check_application(str(shortcut))
    assert result["found"] and result["executable_exists"] is None
    assert "unverified" in result["note"]


def test_missing_and_virtual_alias_do_not_claim_installation(safe_lookup):
    assert app_lookup.check_application("Nonexistent fixture app")["found"] is False
    alias = app_lookup.check_application("gmail")
    assert alias["found"] is False and alias["executable_exists"] is None
    assert alias["launch_tested"] is False


def test_absolute_path_survives_unrelated_shortcut_scan(safe_lookup):
    folder = safe_lookup / "user/Microsoft/Windows/Start Menu/Programs"; folder.mkdir(parents=True)
    (folder / "Other App.lnk").write_bytes(b"fixture")
    exe = safe_lookup / "Exact.exe"; exe.write_bytes(b"fixture")
    result = app_lookup.check_application(str(exe))
    assert result["path"] == str(exe) and result["found"]


@pytest.mark.asyncio
async def test_tool_only_dispatches_check_primitive(monkeypatch):
    import src.tools.os.check_app_exists as module
    bridge = SimpleNamespace(execute_primitive=AsyncMock(return_value={"found": True, "launch_tested": False}))
    monkeypatch.setattr(module, "DaemonExecutorBridge", lambda: bridge)
    monkeypatch.setattr(module.ApplicationConfigLookup, "find_configured_app", lambda n: "fixture.exe")
    tool = CheckAppExistsTool()
    result = await tool.execute(" Fixture App ")
    assert result["success"]
    bridge.execute_primitive.assert_awaited_once_with(action="check_app_exists", payload={"name": "Fixture App", "explicit_path": "fixture.exe"}, timeout=10.0)
    assert tool.is_side_effecting is False
    assert tool.get_schema()["annotations"]["readOnlyHint"] is True
    assert (await tool.execute(" "))["success"] is False
    assert bridge.execute_primitive.await_count == 1


@pytest.mark.asyncio
async def test_offline_daemon_is_error_not_app_absence(monkeypatch):
    import src.tools.os.check_app_exists as module
    monkeypatch.setattr(module, "DaemonExecutorBridge", lambda: SimpleNamespace(execute_primitive=AsyncMock(side_effect=RuntimeError("offline"))))
    monkeypatch.setattr(module.ApplicationConfigLookup, "find_configured_app", lambda n: None)
    result = await CheckAppExistsTool().execute("App")
    assert result["success"] is False
    assert "offline" in result["error"]


def test_discovery_and_scout_advertise_read_only_app_lookup():
    from src.infrastructure.tools.discovery.filesystem_scanner import FilesystemScanner
    from src.core.orchestration.nodes.scout_node import build_scout_capability_summary
    from src.core.orchestration.scout_tool_policy import scout_tool_allowed
    from src.core.tools.partition import TOOL_MANIFEST, ToolPartition
    registry = MagicMock()
    FilesystemScanner(registry)._load_tools_from_file(Path("src/tools/os/check_app_exists.py"), "os")
    tool = registry.register_tool.call_args.args[0]
    assert tool.name == "check_app_exists"
    assert registry.register_tool.call_args.kwargs["category"] == "os"
    registry.get_all_schemas.return_value = [tool.get_schema()]
    summary = build_scout_capability_summary(registry, set())
    assert "Installed-app lookup" in summary and "check_app_exists" in summary
    assert scout_tool_allowed(tool.name)
    assert TOOL_MANIFEST[tool.name] == ToolPartition.LOCAL


def test_launch_uses_same_resolver_but_only_launch_action_spawns(safe_lookup, monkeypatch):
    import subprocess
    exe = safe_lookup / "App.exe"; exe.write_bytes(b"fixture")
    spawn = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", spawn)
    daemon = HandsDaemon.__new__(HandsDaemon)
    daemon._execute_primitive(DaemonAction.LAUNCH_APP, {"name": "App", "explicit_path": str(exe), "args": ["--fixture"]})
    assert spawn.call_args.args[0] == [str(exe), "--fixture"]
