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
    assert "system prompt" in result["full_content"]


@pytest.mark.asyncio
async def test_read_file_failure_has_no_trust_marker(tmp_path):
    tool = ReadLocalFileTool()
    result = await tool.execute(str(tmp_path / "does_not_exist.md"))

    assert result["success"] is False
    assert "_trusted_source" not in result


@pytest.mark.asyncio
async def test_read_file_pagination_and_line_numbers(tmp_path):
    f = tmp_path / "lines.txt"
    f.write_text("\n".join(f"Line content {i}" for i in range(1, 251)), encoding="utf-8")

    tool = ReadLocalFileTool()
    # Read first 100 lines
    res1 = await tool.execute(str(f), offset=1, limit=100)
    assert res1["success"] is True
    assert res1["offset"] == 1
    assert res1["limit"] == 100
    assert res1["total_lines"] == 250
    assert res1["has_more"] is True
    assert "   1 | Line content 1" in res1["content"]
    assert " 100 | Line content 100" in res1["content"]
    assert "Line content 101" not in res1["content"]
    assert "Call read_file with offset=101 to read more" in res1["content"]

    # Read next 150 lines
    res2 = await tool.execute(str(f), offset=101, limit=150)
    assert res2["success"] is True
    assert res2["has_more"] is False
    assert " 101 | Line content 101" in res2["content"]
    assert " 250 | Line content 250" in res2["content"]
    assert "(end of file)" in res2["result"]


@pytest.mark.asyncio
async def test_read_file_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")

    tool = ReadLocalFileTool()
    res = await tool.execute(str(f))
    assert res["success"] is True
    assert res["total_lines"] == 0
    assert res["has_more"] is False
    assert "empty" in res["content"]


@pytest.mark.asyncio
async def test_read_file_query_jump(tmp_path):
    f = tmp_path / "code.py"
    lines = [f"# Line {i}" for i in range(1, 150)]
    lines.append("class TargetClassDefinition:")
    lines.extend([f"# Follow up {i}" for i in range(1, 50)])
    f.write_text("\n".join(lines), encoding="utf-8")

    tool = ReadLocalFileTool()
    res = await tool.execute(str(f), query="TargetClassDefinition", limit=20)
    assert res["success"] is True
    # Should start around line 145 (5 lines before 150)
    assert "class TargetClassDefinition:" in res["content"]
    assert "jumped to line 150 matching 'TargetClassDefinition'" in res["content"]
