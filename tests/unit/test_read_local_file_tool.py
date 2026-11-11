"""Unit tests for ReadLocalFileTool (#642 — trusted-source provenance marker)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tools.filesystem.discovery_tools import ReadLocalFileTool


@pytest.mark.asyncio
async def test_read_file_marks_output_trusted(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("Some notes about the system prompt and new instructions.", encoding="utf-8")

    tool = ReadLocalFileTool()
    result = await tool.execute(str(f))

    assert result["success"] is True
    assert result["_trusted_source"] is True
    assert result["full_content"] == "Some notes about the system prompt and new instructions."


@pytest.mark.asyncio
async def test_read_file_failure_has_no_trust_marker(tmp_path):
    tool = ReadLocalFileTool()
    result = await tool.execute(str(tmp_path / "does_not_exist.md"))

    assert result["success"] is False
    assert "_trusted_source" not in result


@pytest.mark.asyncio
async def test_read_file_pages_advance_by_line_without_returning_unread_text(tmp_path):
    path = tmp_path / "pages.txt"
    path.write_text("first\nsecond\nthird\nfourth\n", encoding="utf-8")
    tool = ReadLocalFileTool()

    first = await tool.execute(str(path), limit=2)
    assert first["content"] == "   1 | first\n   2 | second"
    assert first["total_lines"] == 4
    assert first["has_more"] and first["truncated"]
    assert "full_content" not in first
    assert (first["next_offset"], first["next_column"]) == (3, 1)

    second = await tool.execute(
        str(path), offset=first["next_offset"], column=first["next_column"], limit=2,
    )
    assert second["content"] == "   3 | third\n   4 | fourth"
    assert not second["has_more"] and not second["truncated"]
    assert "next_offset" not in second


@pytest.mark.asyncio
async def test_read_file_finds_literal_symbol_at_or_after_offset(tmp_path):
    path = tmp_path / "example.py"
    path.write_text("target()\nother()\nTarget()\n", encoding="utf-8")
    tool = ReadLocalFileTool()

    result = await tool.execute(str(path), offset=2, query="target(", limit=1)
    assert result["matched_line"] == 3
    assert result["matched_column"] == 1
    assert result["content"] == "   3 | Target()"

    missed = await tool.execute(str(path), offset=2, query="missing", limit=1)
    assert missed["matched_line"] is None
    assert missed["content"] == "   2 | other()"


@pytest.mark.asyncio
async def test_read_file_preserves_long_unicode_line_and_cursor_under_output_budget(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from src.core.tools import limits, repetition
    from src.infrastructure.tools.registry import ToolRegistry
    from src.infrastructure.tools.schema.schema_builder import ToolSchemaBuilder

    get_limit = limits.get
    budget = 1000
    monkeypatch.setattr(limits, "get", lambda key: budget if key == "tool_output_max_chars" else get_limit(key))
    text = 'é\\"' * 150 + "Target()" + 'é\\"' * 150
    path = tmp_path / "long.txt"
    path.write_text(text, encoding="utf-8")
    tool = ReadLocalFileTool()
    registry = object.__new__(ToolRegistry)
    registry._tools = {tool.name: tool}
    registry._event_bus = None
    registry._schema_builder = ToolSchemaBuilder(registry)

    async def execute(tool, parameters):
        return await tool.execute(**parameters)

    registry._routing_engine = SimpleNamespace(
        route_to_executor=lambda tool: SimpleNamespace(execute=execute),
    )
    repetition.reset()
    wrapped = registry.get_langchain_tool_by_name(tool.name)
    cursor = (1, 1)
    pieces = []
    saw_hint = False

    while cursor:
        message = await wrapped.ainvoke({
            "name": tool.name,
            "args": {"file_path": str(path), "offset": cursor[0], "column": cursor[1]},
            "type": "tool_call",
            "id": f"file-page-{len(pieces)}",
        })
        assert len("[TOOL_DATA]\n" + message.content) <= budget
        result = json.loads(message.content)
        assert result["success"] is True, result
        saw_hint |= "harness_hint" in result
        assert "full_content" not in result
        pieces.append(result["content"].split(" | ", 1)[1])
        next_cursor = (result["next_offset"], result["next_column"]) if result["has_more"] else None
        assert next_cursor is None or next_cursor > cursor
        cursor = next_cursor

    assert saw_hint
    assert "".join(pieces) == text
    matched = await tool.execute(str(path), query="target()")
    assert matched["matched_line"] == 1
    assert matched["matched_column"] == len('é\\"' * 150) + 1
    assert matched["content"].startswith("   1 | Target()")


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "offset", "message"), [
    ("", 1, "File is empty"),
    ("one line\n", 2, "Offset is beyond the end of the file"),
])
async def test_read_file_reports_empty_and_out_of_range_pages(tmp_path, text, offset, message):
    path = tmp_path / "page.txt"
    path.write_text(text, encoding="utf-8")

    result = await ReadLocalFileTool().execute(str(path), offset=offset)

    assert result["success"] is True
    assert result["content"] == ""
    assert result["result"] == message
    assert not result["has_more"]


@pytest.mark.asyncio
async def test_read_file_preserves_home_relative_resolution(tmp_path, monkeypatch):
    from src.shared.fs_sandbox import ENV_VAR

    home = tmp_path / "home"
    cwd = tmp_path / "working"
    home.mkdir()
    cwd.mkdir()
    (home / "example.txt").write_text("from home", encoding="utf-8")
    (cwd / "example.txt").write_text("from cwd", encoding="utf-8")
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(cwd)

    result = await ReadLocalFileTool().execute("example.txt")

    assert result["full_content"] == "from home"


@pytest.mark.asyncio
async def test_read_file_rejects_a_zero_offset(tmp_path):
    result = await ReadLocalFileTool().execute(str(tmp_path / "page.txt"), offset=0)

    assert result["failure_class"] == "bad_input"
    assert "offset" in result["detail"]
