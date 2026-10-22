from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from src.core.orchestration.nodes import scout_node as scout
from src.core.orchestration.scout_evidence import report_tool, validate_report_sources


def test_schema_exposes_every_report_field_to_model():
    from src.infrastructure.tools.schema.schema_builder import parameters_for_llm_from_tool
    schema = parameters_for_llm_from_tool(report_tool())
    item = schema["properties"]["items"]["items"]
    assert "source_call_id" in item["properties"]
    assert set(item["required"]) == {"question", "status", "finding", "source", "source_call_id"}
    assert item["properties"]["status"]["enum"] == ["verified", "unresolved", "unavailable"]
    assert "$ref" not in str(schema)


@pytest.mark.asyncio
async def test_exact_lookup_ignores_wrong_category(monkeypatch):
    from src.application.events.event_bus import EventBus
    monkeypatch.setattr(EventBus, "get_instance", lambda: MagicMock())
    tool = SimpleNamespace(ainvoke=AsyncMock())
    registry = SimpleNamespace(get_all_schemas=lambda: [{"name": "check_app_exists", "parameters": {}}])
    out = await scout._execute_scout_tool("search_for_tool", {"query": "check app exists", "category": "system"}, {"search_for_tool": tool}, registry, {}, "root")
    assert out["result"]["_injected_schemas"][0]["name"] == "check_app_exists"
    tool.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_no_permitted_category_match_retries_once_broadly(monkeypatch):
    from src.application.events.event_bus import EventBus
    monkeypatch.setattr(EventBus, "get_instance", lambda: MagicMock())
    tool = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        {"result": {"_injected_schemas": [{"name": "escalate_to_agent"}]}},
        {"result": {"_injected_schemas": [{"name": "check_app_exists"}]}},
    ]))
    registry = SimpleNamespace(get_all_schemas=lambda: [], _tools={})
    out = await scout._execute_scout_tool("search_for_tool", {"query": "installed application lookup", "category": "system"}, {"search_for_tool": tool}, registry, {}, "root")
    assert out["scout_retried_without_category"]
    assert tool.ainvoke.await_args_list[1].args[0] == {"query": "installed application lookup"}
    assert tool.ainvoke.await_count == 2


def test_validation_error_names_field_and_usable_reference():
    error = validate_report_sources([{"question": "Recipient", "status": "verified", "finding": "person@example.test", "source": "memory"}], [{"tool": "manage_user_memory", "call_id": "memory-actual-1", "outcome": "returned"}], "")
    assert "Item 1 (Recipient)" in error
    assert "source_call_id" in error and "memory-actual-1" in error


def test_loaded_tool_menu_updates_after_discovery():
    prompt = "### LOADED TOOLS\n- old\n\n### USER REQUEST\nTask"
    updated = scout._refresh_loaded_tools(prompt, [SimpleNamespace(name="check_app_exists", description="Read-only app lookup")])
    assert "`check_app_exists`" in updated and "- old" not in updated
