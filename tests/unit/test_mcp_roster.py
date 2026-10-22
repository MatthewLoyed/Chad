"""ACTIVE MCP CONNECTORS roster for the supervisor prompt."""

from src.infrastructure.tools.mcp_roster import (
    build_active_mcp_roster,
    connected_mcp_server_names,
)


def test_roster_empty():
    text = build_active_mcp_roster([])
    assert "ACTIVE MCP CONNECTORS" in text
    assert "none connected" in text
    assert "call_mcp_tool" in text


def test_roster_lists_known_hints():
    text = build_active_mcp_roster(["canva", "github"])
    assert "**canva**" in text
    assert "graphic design" in text
    assert "**github**" in text
    assert "Settings → Connectors" in text


def test_connected_names_from_registry():
    class FakeReg:
        def get_servers(self):
            return {"canva": object(), "excalidraw": object()}

    class FakeTools:
        mcp_registry = FakeReg()

    assert connected_mcp_server_names(FakeTools()) == ["canva", "excalidraw"]
    assert connected_mcp_server_names(None) == []
