"""Unit tests for canvas_agent registration, handoff tools, and supervisor delegation routing."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.orchestration.agents.factory import AGENT_CONFIGS
from src.core.orchestration.handoffs import create_handoff_tools
from src.core.orchestration.plan_step_resources import DELEGATE_STEP_TOOLS
from src.infrastructure.tools.delegate_routing import (
    SUPERVISOR_EXCLUDED_CATEGORIES,
    CATEGORY_TO_DELEGATE_AGENT,
    STEP_TOOL_TO_DELEGATE,
    is_supervisor_delegate_only,
)
from src.infrastructure.tools.schema.schema_builder import ToolSchemaBuilder
from src.shared.logger import ColoredFormatter
import logging


def test_canvas_agent_in_agent_configs():
    assert "canvas_agent" in AGENT_CONFIGS
    cfg = AGENT_CONFIGS["canvas_agent"]
    assert cfg["domain"] == "canvas"
    assert cfg["scope"] == "canvas_agent"


def test_canvas_agent_in_delegate_step_tools():
    assert "delegate_to_canvas_agent" in DELEGATE_STEP_TOOLS


def test_canvas_excluded_from_supervisor():
    assert "canvas" in SUPERVISOR_EXCLUDED_CATEGORIES
    assert CATEGORY_TO_DELEGATE_AGENT.get("canvas") == "canvas_agent"

    categories = {"canvas": ["get_canvas_courses", "get_course_modules"]}
    tool_sources = {}
    assert is_supervisor_delegate_only("get_canvas_courses", categories, tool_sources)
    assert is_supervisor_delegate_only("get_course_modules", categories, tool_sources)


def test_canvas_step_tools_mapped_to_delegate():
    assert STEP_TOOL_TO_DELEGATE.get("get_course_modules") == "delegate_to_canvas_agent"
    assert STEP_TOOL_TO_DELEGATE.get("search_module_items") == "delegate_to_canvas_agent"
    assert STEP_TOOL_TO_DELEGATE.get("download_course_file") == "delegate_to_canvas_agent"


def test_canvas_domain_in_schema_builder():
    mock_reg = MagicMock()
    mock_reg._tools = {}
    mock_reg._categories = {}
    mock_reg._tool_sources = {}
    builder = ToolSchemaBuilder(mock_reg)
    tools = builder.get_tools_by_domain("canvas")
    assert isinstance(tools, list)


def test_create_handoff_tools_creates_canvas_delegate():
    fake_canvas_agent = MagicMock()
    fake_canvas_agent.aget_state = AsyncMock(return_value=None)
    fake_canvas_agent.ainvoke = AsyncMock(
        return_value={"messages": [MagicMock(content="Downloaded 3 lectures.")]}
    )

    tools = create_handoff_tools({"canvas_agent": fake_canvas_agent})
    tool_names = [t.name for t in tools]
    assert "delegate_to_canvas_agent" in tool_names


def test_colored_formatter_strips_ansi_when_no_color():
    formatter = ColoredFormatter(fmt="%(levelname)s - %(message)s", use_colors=False)
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="test message",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "\033" not in formatted
    assert "INFO - test message" in formatted
