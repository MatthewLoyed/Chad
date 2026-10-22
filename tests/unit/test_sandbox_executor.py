"""LocalExecutor sandbox loop-breaker tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.tools.execution.executor_strategy import LocalExecutor
from src.tools.base_tool import BaseTool


class _SandboxTool(BaseTool):
    name = "run_sandboxed_bash"
    description = "test"
    category = "coding"

    def __init__(self, results):
        self._results = list(results)

    def get_schema(self):
        return {"name": self.name, "description": self.description, "parameters": {"type": "object", "properties": {}}}

    async def execute(self, **kwargs):
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_local_executor_blocks_identical_failing_sandbox_call():
    executor = LocalExecutor()
    tool = _SandboxTool([
        {"success": False, "error": "command not found", "retryable": True}
    ])

    first = await executor.execute(tool, {"command": "date"})
    second = await executor.execute(tool, {"command": "date"})

    # The tool returned a bare error; the executor classified it (shared vocabulary).
    assert first["failure_class"] == "not_found"
    assert second["failure_class"] == "blocked"
    assert second["retryable"] is False
    assert "NEXT_ACTIONS" in second["error"]


@pytest.mark.asyncio
async def test_local_executor_reclassifies_an_unknown_failure_class():
    """A tool using a vocabulary we don't know gets re-classified rather than
    passed through — otherwise `retryable` is whatever that tool felt like."""
    executor = LocalExecutor()
    tool = _SandboxTool([
        {"success": False, "error": "401: Unauthorized", "failure_class": "legacy_thing"}
    ])
    result = await executor.execute(tool, {"command": "date"})
    assert result["failure_class"] == "auth"
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_local_executor_blocks_all_sandbox_tools_after_auth_failure():
    executor = LocalExecutor()
    bash = _SandboxTool([
        {
            "success": False,
            "error": "401: Unauthorized",
            "failure_class": "auth",
            "retryable": False,
        }
    ])
    python = _SandboxTool([{"success": True, "stdout": "ok"}])
    python.name = "run_sandboxed_python"

    await executor.execute(bash, {"command": "date"})
    blocked = await executor.execute(python, {"code": "print(1)"})

    assert blocked["failure_class"] == "auth"
    assert blocked["retryable"] is False
    assert "already failed" in blocked["error"].lower()
