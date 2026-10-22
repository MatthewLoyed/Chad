"""
Risk-based (policy-driven) approval gate tests.

Covers the gap that the per-tool requires_approval flag couldn't: tools with no
Python class to set the flag (MCP tools, skills) are gated by name patterns in
config/tool_policies.yaml and enforced by the executor-agnostic approval gate.
"""

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import src.core.tools.tool_policy_manager as tpm_module
from src.core.tools.tool_policy_manager import ToolPolicyManager
from src.core.orchestration import approval_store
from src.core.orchestration.context import session_id_var
from src.infrastructure.tools.execution import executor_strategy
from src.infrastructure.tools.execution.executor_strategy import (
    MCPExecutor,
    SkillExecutor,
    _policy_approval_gate,
)


def _manager_with(patterns, enabled=True, groups=None):
    mgr = ToolPolicyManager()
    mgr._tool_groups = groups or {}
    mgr._approval_enabled = enabled
    mgr._approval_patterns = mgr._expand_group_refs(patterns)
    return mgr


class TestRequiresApproval:
    def test_exact_and_wildcard_match(self):
        mgr = _manager_with(["execute_sql", "*_send", "*_delete"])
        assert mgr.requires_approval("execute_sql") is True
        assert mgr.requires_approval("gmail_send") is True
        assert mgr.requires_approval("file_delete") is True
        assert mgr.requires_approval("web_search") is False

    def test_disabled_gate_never_matches(self):
        mgr = _manager_with(["*"], enabled=False)
        assert mgr.requires_approval("execute_sql") is False

    def test_group_expansion(self):
        mgr = _manager_with(["@danger"], groups={"danger": ["delete_branch", "reset_branch"]})
        assert mgr.requires_approval("delete_branch") is True
        assert mgr.requires_approval("reset_branch") is True
        assert mgr.requires_approval("list_branches") is False


@pytest.mark.asyncio
class TestExecutorPolicyGate:
    def setup_method(self):
        approval_store._pending.clear()
        self.token = session_id_var.set("conv-policy-test")
        self._saved_singleton = tpm_module._policy_manager
        tpm_module._policy_manager = _manager_with(["send_email", "execute_sql"])

    def teardown_method(self):
        session_id_var.reset(self.token)
        tpm_module._policy_manager = self._saved_singleton

    async def test_policy_gate_pauses_then_proceeds(self):
        # First touch: gated tool pauses for approval.
        first = _policy_approval_gate("send_email")
        assert first is not None and first["_pending_approval"] is True
        token = first["approval_token"]

        # Approve -> gate clears and lets execution proceed (returns None).
        approval_store.resolve(token, "approved")
        assert _policy_approval_gate("send_email") is None

    async def test_policy_gate_declined(self):
        first = _policy_approval_gate("execute_sql")
        approval_store.resolve(first["approval_token"], "declined")
        second = _policy_approval_gate("execute_sql")
        assert second["_approval_declined"] is True

    async def test_ungated_tool_skips(self):
        assert _policy_approval_gate("web_search") is None

    async def test_mcp_executor_pauses_gated_tool(self):
        class _FakeServer:
            name = "supabase"

            async def call_tool(self, name, params):  # pragma: no cover - must NOT run
                raise AssertionError("gated MCP tool executed without approval")

        tool = {"mcp_server": _FakeServer(), "name": "execute_sql"}
        result = await MCPExecutor().execute(tool, {"query": "DROP TABLE x"})
        assert result["_pending_approval"] is True

    async def test_skill_executor_pauses_gated_skill(self):
        from src.core.skill_system.base_skill import BaseSkill

        class _SendSkill(BaseSkill):
            name = "send_email"

            def _setup_tools(self):
                return None

            def get_system_prompt(self):
                return ""

            async def execute(self, **kwargs):  # pragma: no cover - must NOT run
                raise AssertionError("gated skill executed without approval")

        # Build via __new__ to skip BaseSkill constructor wiring — the test only
        # exercises the gate, which keys off skill.name.
        skill = _SendSkill.__new__(_SendSkill)
        skill.name = "send_email"
        result = await SkillExecutor().execute(skill, {})
        assert result["_pending_approval"] is True
