"""Executor loop-breaker state must not outlive its turn.

Executors are instantiated once on a process-lifetime ExecutionRoutingEngine, so
anything they accumulate survives until the Brain restarts. A call that failed
once and was never retried used to stay blocked forever, in every later
conversation, with no way to clear it short of a restart.
"""

import pytest

from src.core.orchestration.context import turn_id_var
from src.infrastructure.tools.execution.executor_strategy import LocalExecutor, MCPExecutor
from src.tools.base_tool import BaseTool


class _SandboxTool(BaseTool):
    name = "run_sandboxed_bash"
    description = "test"
    category = "coding"

    def __init__(self, results):
        self._results = list(results)

    def get_schema(self):
        return {"name": self.name, "description": self.description,
                "parameters": {"type": "object", "properties": {}}}

    async def execute(self, **kwargs):
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_sandbox_block_does_not_leak_into_the_next_turn():
    executor = LocalExecutor()
    failing = {"success": False, "error": "command not found"}

    token = turn_id_var.set("turn-1")
    try:
        tool = _SandboxTool([failing])
        await executor.execute(tool, {"command": "date"})
        # Same turn: the identical call is refused.
        blocked = await executor.execute(_SandboxTool([failing]), {"command": "date"})
        assert blocked["failure_class"] == "blocked"
    finally:
        turn_id_var.reset(token)

    token = turn_id_var.set("turn-2")
    try:
        tool = _SandboxTool([{"success": True, "stdout": "ok"}])
        result = await executor.execute(tool, {"command": "date"})
        assert result["success"] is True, "stale block leaked across turns"
    finally:
        turn_id_var.reset(token)


@pytest.mark.asyncio
async def test_sandbox_auth_latch_clears_with_the_turn():
    """A bad E2B key is a real reason to stop trying — for this turn. The user may
    fix it in Settings before the next one."""
    executor = LocalExecutor()

    token = turn_id_var.set("turn-1")
    try:
        await executor.execute(
            _SandboxTool([{"success": False, "error": "401 Unauthorized", "failure_class": "auth"}]),
            {"command": "date"},
        )
        assert executor._sandbox_auth_failed is True
    finally:
        turn_id_var.reset(token)

    token = turn_id_var.set("turn-2")
    try:
        result = await executor.execute(
            _SandboxTool([{"success": True, "stdout": "ok"}]), {"command": "date"}
        )
        assert result["success"] is True
        assert executor._sandbox_auth_failed is False
    finally:
        turn_id_var.reset(token)


def test_browser_counters_clear_with_the_turn():
    """The page changes between turns, so a ban from an old page state is stale —
    exactly the case where a retry should be allowed."""
    executor = MCPExecutor()

    token = turn_id_var.set("turn-1")
    try:
        executor._reset_state_on_new_turn()
        executor._browser_call_failure_counts["browser_click:{}"] = 3
    finally:
        turn_id_var.reset(token)

    token = turn_id_var.set("turn-2")
    try:
        executor._reset_state_on_new_turn()
        assert executor._browser_call_failure_counts == {}
    finally:
        turn_id_var.reset(token)


def test_state_survives_within_one_turn():
    executor = MCPExecutor()
    token = turn_id_var.set("turn-stable")
    try:
        executor._reset_state_on_new_turn()
        executor._browser_call_failure_counts["browser_click:{}"] = 1
        executor._reset_state_on_new_turn()
        assert executor._browser_call_failure_counts == {"browser_click:{}": 1}
    finally:
        turn_id_var.reset(token)
