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
