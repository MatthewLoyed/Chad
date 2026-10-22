from unittest.mock import MagicMock, patch
from src.core.orchestration.agents.factory import create_specialist_agents
from src.infrastructure.tools.execution.snapshot_pruner import render_set_of_mark


def test_browser_agent_disabled_when_playwright_tools_missing():
    """Verify browser_agent is NOT included in supervisor specialist team if Playwright is offline."""
    mock_registry = MagicMock()
    # Simulate registry where Playwright failed to connect (only browser_scroll present, no navigate/snapshot)
    scroll_tool = MagicMock()
    scroll_tool.name = "browser_scroll"
    mock_registry.get_langchain_tools_by_domain.return_value = [scroll_tool]

    with patch("src.shared.config_manager.ConfigManager.get", side_effect=lambda key, default=None: [] if key == "disabled_subagents" else default), patch("src.core.orchestration.agents.factory.build_specialist_graph", return_value=MagicMock()):
        agents = create_specialist_agents(model=MagicMock(), tool_registry=mock_registry)
    assert "browser_agent" not in agents, "browser_agent must NOT be accessible when Playwright tools are missing!"


def test_browser_agent_enabled_when_playwright_tools_present():
    """Verify browser_agent IS included when Playwright tools are online."""
    mock_registry = MagicMock()
    t1 = MagicMock()
    t1.name = "browser_navigate"
    t2 = MagicMock()
    t2.name = "browser_snapshot"
    t3 = MagicMock()
    t3.name = "browser_scroll"
    mock_registry.get_langchain_tools_by_domain.return_value = [t1, t2, t3]

    with patch("src.shared.config_manager.ConfigManager.get", side_effect=lambda key, default=None: [] if key == "disabled_subagents" else default), patch("src.core.orchestration.agents.factory.build_specialist_graph", return_value=MagicMock()):
        agents = create_specialist_agents(model=MagicMock(), tool_registry=mock_registry)
    assert "browser_agent" in agents, "browser_agent should be accessible when Playwright is online!"


def test_render_set_of_mark_filters_layout_roles():
    """Verify render_set_of_mark excludes table, row, cell, generic and includes buttons and links."""
    sample_yaml = """
- table [ref=e1]:
  - rowgroup [ref=e2]:
    - row [ref=e3]:
      - cell [ref=e4]:
        - button "Submit Order" [ref=e5] [cursor=pointer]
        - generic [ref=e6]:
          - link "Product Link" [ref=e7] [cursor=pointer]
    """
    som = render_set_of_mark(sample_yaml)
    entry_lines = [l for l in som.splitlines() if l.startswith("[")]
    assert len(entry_lines) == 2
    assert any('button "Submit Order"  ->  target="e5"' in l for l in entry_lines)
    assert any('link "Product Link"  ->  target="e7"' in l for l in entry_lines)
    assert not any("table" in l for l in entry_lines)
    assert not any("rowgroup" in l for l in entry_lines)
    assert not any("row" in l for l in entry_lines)
    assert not any("cell" in l for l in entry_lines)
    assert not any("generic" in l for l in entry_lines)
