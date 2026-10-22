import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.tools import ToolRegistry


def _register_mcp_tool(registry: ToolRegistry, name: str, server_name: str):
    mock_server = MagicMock()
    mock_server.name = server_name
    mock_server.config = {}
    registry.register_mcp_tool(
        tool_metadata={
            "name": name,
            "description": f"MCP tool {name}",
            "inputSchema": {"type": "object", "properties": {}},
        },
        server=mock_server,
    )


def _cfg_side_effect(key, default=None):
    if key == "tools.pinned_mcp_tools":
        return None
    if key.startswith("tools.mcp_auto_pin"):
        return False if key.endswith("enabled") else default
    if key == "tools.decompose_queries":
        return True
    if key == "tools.llm_selector_model":
        return "qwen/qwen3.5-9b"
    if key == "local_models.providers.lmstudio":
        return {"base_url": "http://localhost:1234/v1"}
    return default


class TestMCPSelectorExposure:

    @patch("src.infrastructure.tools.registry.LLMToolSelector")
    @patch("src.shared.config_manager.ConfigManager.get")
    def test_selector_picked_mcp_exposed(self, mock_cfg_get, mock_llm_cls):
        mock_cfg_get.side_effect = _cfg_side_effect
        mock_selector = mock_llm_cls.return_value
        mock_selector.is_available.return_value = True
        mock_selector.select.return_value = ["fixture_search", "fixture_listFolder"]

        registry = ToolRegistry()
        registry.discover_all()
        _register_mcp_tool(registry, "fixture_search", "fixture-docs")
        _register_mcp_tool(registry, "fixture_listFolder", "fixture-docs")
        _register_mcp_tool(registry, "fixture_addComment", "fixture-docs")

        result = registry.get_schemas_for_turn(
            "search the fixture connector and list its records", category="planning"
        )
        names = [s["name"] for s in result["schemas"]]

        assert "fixture_search" in names
        assert "fixture_listFolder" in names
        assert "fixture_addComment" not in names

    @patch("src.infrastructure.tools.registry.LLMToolSelector")
    @patch("src.shared.config_manager.ConfigManager.get")
    def test_unselected_mcp_not_exposed(self, mock_cfg_get, mock_llm_cls):
        mock_cfg_get.side_effect = _cfg_side_effect
        mock_selector = mock_llm_cls.return_value
        mock_selector.is_available.return_value = True
        mock_selector.select.return_value = ["hubspace_control"]

        registry = ToolRegistry()
        registry.discover_all()
        _register_mcp_tool(registry, "search", "google-drive")

        result = registry.get_schemas_for_turn("turn on the lamp")
        names = [s["name"] for s in result["schemas"]]

        assert "hubspace_control" in names
        assert "search" not in names

    @patch("src.infrastructure.tools.registry.LLMToolSelector")
    @patch("src.shared.config_manager.ConfigManager.get")
    def test_mcp_gateway_bundle_expands(self, mock_cfg_get, mock_llm_cls):
        mock_cfg_get.side_effect = _cfg_side_effect
        mock_selector = mock_llm_cls.return_value
        mock_selector.is_available.return_value = True
        mock_selector.select.return_value = ["call_mcp_tool"]

        registry = ToolRegistry()
        registry.discover_all()

        result = registry.get_schemas_for_turn("use github mcp")
        names = [s["name"] for s in result["schemas"]]

        # mcp_gateway expands to call_mcp_tool + list_directory + read_file.
        # list_directory/read_file are bindable on the supervisor for quick lookups
        # (explore still routes via prompts to traversal). write_file stays
        # supervisor-delegate-only (code_agent).
        assert "call_mcp_tool" in names
        assert "write_file" not in names

    @patch("src.infrastructure.tools.registry.LLMToolSelector")
    @patch("src.shared.config_manager.ConfigManager.get")
    def test_mcp_binds_for_supervisor(self, mock_cfg_get, mock_llm_cls):
        mock_cfg_get.side_effect = _cfg_side_effect
        mock_selector = mock_llm_cls.return_value
        mock_selector.is_available.return_value = True
        mock_selector.select.return_value = ["search", "listFolder"]

        registry = ToolRegistry()
        registry.discover_all()
        _register_mcp_tool(registry, "search", "google-drive")
        _register_mcp_tool(registry, "listFolder", "google-drive")

        schemas = registry.get_schemas_for_turn("list my drive wedding folder")["schemas"]
        for schema in schemas:
            name = schema["name"]
            if name in ("search", "listFolder"):
                assert registry.get_langchain_tool_by_name(name) is not None

    @patch("src.infrastructure.tools.registry.LLMToolSelector")
    @patch("src.shared.config_manager.ConfigManager.get")
    def test_google_drive_bundle_expands_and_pairs_skill(self, mock_cfg_get, mock_llm_cls):
        mock_cfg_get.side_effect = _cfg_side_effect
        mock_selector = mock_llm_cls.return_value
        mock_selector.is_available.return_value = True
        mock_selector.select.return_value = ["google_drive_search"]

        registry = ToolRegistry()
        registry.discover_all()

        result = registry.get_schemas_for_turn(
            "find wedding roadmap in my google drive wedding folder"
        )
        names = [s["name"] for s in result["schemas"]]

        # google_drive_* and local FS atoms (fuzzy_find_local/list_directory/read_file)
        # are bindable on the supervisor for known-target / quick lookups; explore /
        # search-through still goes through delegate_to_traversal_agent via prompts.
        # The Drive bundle pairs the google_drive skill.
        assert "google_drive_search" in names
        assert result.get("bundled_skills") == ["google_drive"]
        assert result["selection"].get("bundled_skills") == ["google_drive"]
