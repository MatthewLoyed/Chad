import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.tools import ToolRegistry


def test_list_catalog_entries_reports_local_source():
    registry = ToolRegistry()
    registry.discover_all()
    entries = registry.list_catalog_entries()
    assert entries
    file_manager = next(e for e in entries if e["name"] == "file_manager")
    assert file_manager["source"] == "local"
    assert file_manager["server_name"] is None


def test_list_catalog_entries_reports_mcp_server_name():
    registry = ToolRegistry()
    server = SimpleNamespace(name="test_mcp_server")
    registry.register_mcp_tool(
        {
            "name": "test_mcp_tool",
            "description": "A fake MCP tool for testing.",
            "parameters": {"type": "object", "properties": {}},
        },
        server,
    )
    entries = registry.list_catalog_entries()
    entry = next(e for e in entries if e["name"] == "test_mcp_tool")
    assert entry["source"] == "mcp"
    assert entry["server_name"] == "test_mcp_server"
