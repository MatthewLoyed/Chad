from types import SimpleNamespace
from src.infrastructure.tools.capability_directory import build_capability_directory


def test_directory_reports_actual_native_tools_without_loading_schemas():
    registry = SimpleNamespace(
        _categories={}, _tool_sources={"search_github": "mcp:github"},
        get_all_schemas=lambda: [
            {"name": "search_gmail"}, {"name": "web_search"},
            {"name": "google_drive_search"}, {"name": "ui_click"},
            {"name": "search_github"},
        ],
    )
    text = build_capability_directory(registry, {"web_search"})
    assert "search_gmail [DISCOVER]" in text
    assert "web_search [LOADED]" in text
    assert "google_drive_search [DISCOVER]" in text
    assert "ui_click" not in text
    assert "search_github" not in text
    assert "Calendar:" not in text
    assert "search_for_tool" in text


def test_directory_is_bounded_and_deterministic():
    names = [f"get_calendar_event_{i}" for i in range(30)]
    def build(values):
        registry = SimpleNamespace(_categories={}, _tool_sources={},
                                   get_all_schemas=lambda: [{"name": n} for n in values])
        return build_capability_directory(registry, set())
    first = build(names)
    assert first == build(list(reversed(names)))
    assert first.count("[DISCOVER]") == 2
