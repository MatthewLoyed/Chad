"""Successful empty lookups and scoped filesystem search misses."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.tools.canvas.assignments import GetCanvasDashboardTodosTool
from src.tools.filesystem import discovery_tools
from src.tools.system.utilities.manage_user_memory import ManageUserMemory


@pytest.mark.asyncio
async def test_empty_memory_search_reports_the_current_scope(monkeypatch):
    tool = ManageUserMemory()
    graph = SimpleNamespace(search=AsyncMock(return_value=None))
    monkeypatch.setattr(tool, "_get_graph", AsyncMock(return_value=graph))

    result = await tool.execute(action="search", query="favourite color")

    assert result["success"] is True
    assert result["count"] == 0
    assert result["results"] == []
    assert result["caption"] == "No facts matched 'favourite color' in the current memory scope."
    graph.search.assert_awaited_once_with("favourite color")


@pytest.mark.asyncio
async def test_empty_canvas_todos_describes_only_the_dashboard(monkeypatch):
    manager = Mock()
    manager.get.return_value = {"success": True, "data": []}
    monkeypatch.setattr("src.tools.canvas.assignments.CanvasManager", lambda: manager)

    result = await GetCanvasDashboardTodosTool().execute()

    assert result["success"] is True
    assert result["todos"] == []
    assert result["result"] == "Canvas returned no items for the dashboard To-Do list."
    manager.get.assert_called_once_with("/users/self/todo")


@pytest.mark.asyncio
@pytest.mark.parametrize("search_root, scope", [
    ("Documents/Example", "Documents/Example"),
    (None, "the sandbox root"),
])
async def test_fuzzy_miss_reports_the_scope(monkeypatch, search_root, scope):
    monkeypatch.setattr("src.shared.fs_sandbox.is_active", lambda: True)
    find = Mock(return_value=[])
    monkeypatch.setattr(discovery_tools._engine, "find", find)

    result = await discovery_tools.FuzzyFindTool().execute(query="notes", search_root=search_root)

    assert result["success"] is False
    assert result["failure_class"] == "not_found"
    assert result["retryable"] is False
    assert result["detail"] == f"Fuzzy filename search returned no matches for 'notes' in {scope}."
    find.assert_called_once_with("notes", search_root or "")


@pytest.mark.asyncio
async def test_fuzzy_failure_surfaces_the_engine_error(monkeypatch):
    detail = "Search root is outside user directory."
    find = Mock(return_value=[{"error": detail}])
    monkeypatch.setattr(discovery_tools._engine, "find", find)

    result = await discovery_tools.FuzzyFindTool().execute(query="notes", search_root="outside")

    assert result["success"] is False
    assert result["failure_class"] != "not_found"
    assert result["detail"] == detail
