import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.tools.execution.executor_strategy import LocalExecutor
from src.tools.base_tool import BaseTool
from src.core.orchestration import approval_store
from src.core.orchestration.context import session_id_var


class FakeApprovalTool(BaseTool):
    """A minimal requires_approval tool used to exercise the executor gate."""

    name = "fake_approval_tool"
    description = "test"
    requires_approval = True
    approval_prompt = "Allow {action}?"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {"action": {"type": "string"}}},
        }

    async def execute(self, action: str, **kwargs) -> Dict[str, Any]:
        return {"success": True, "result": f"did {action}", "decision": kwargs.get("_approval_decision")}


@pytest.mark.asyncio
class TestApprovalGate:

    def setup_method(self):
        approval_store._pending.clear()
        self.token = session_id_var.set("conv-test")

    def teardown_method(self):
        session_id_var.reset(self.token)

    async def test_first_call_short_circuits_pending(self):
        tool = FakeApprovalTool()
        executor = LocalExecutor()

        result = await executor.execute(tool, {"action": "delete file"})

        assert result["_pending_approval"] is True
        assert result["result"] == "Allow delete file?"
        assert "approval_token" in result

    async def test_declined_short_circuits_without_executing(self):
        tool = FakeApprovalTool()
        executor = LocalExecutor()

        pending = await executor.execute(tool, {"action": "delete file"})
        approval_store.resolve(pending["approval_token"], "declined")

        result = await executor.execute(tool, {"action": "delete file"})

        assert result["_approval_declined"] is True
        assert "decision" not in result  # real execute() never ran

    async def test_approved_runs_real_execute_with_decision(self):
        tool = FakeApprovalTool()
        executor = LocalExecutor()

        pending = await executor.execute(tool, {"action": "delete file"})
        approval_store.resolve(pending["approval_token"], "approved")

        result = await executor.execute(tool, {"action": "delete file"})

        assert result["result"] == "did delete file"
        assert result["decision"] == "approved"

    async def test_non_approval_tool_skips_gate_entirely(self):
        class PlainTool(BaseTool):
            name = "plain_tool"
            description = "test"

            def get_schema(self) -> Dict[str, Any]:
                return {"name": self.name, "description": self.description, "parameters": {"type": "object", "properties": {}}}

            async def execute(self, **kwargs) -> Dict[str, Any]:
                return {"success": True, "result": "ran"}

        result = await LocalExecutor().execute(PlainTool(), {})
        assert result == {"success": True, "result": "ran"}
