"""Memory tool mutations must not rely on a model obeying consent prose (#695)."""
from unittest.mock import AsyncMock

import pytest

from src.core.orchestration import approval_store
from src.core.orchestration.context import pending_approval_var, session_id_var, turn_category_var
from src.infrastructure.tools.execution.executor_strategy import LocalExecutor
from src.tools.system.utilities.manage_user_memory import ManageUserMemory


@pytest.fixture(autouse=True)
def isolated_approval_context():
    session = session_id_var.set("alpha-memory-consent-test")
    category = turn_category_var.set("agent")
    pending = pending_approval_var.set(None)
    yield
    for key, entry in list(approval_store._pending.items()):
        if entry["payload"].get("conversation_id") == "alpha-memory-consent-test":
            approval_store._pending.pop(key, None)
    pending_approval_var.reset(pending)
    turn_category_var.reset(category)
    session_id_var.reset(session)


@pytest.mark.asyncio
async def test_save_waits_for_consent_then_executes_exact_fact():
    tool = ManageUserMemory()
    tool._save = AsyncMock(return_value={"success": True, "result": "saved"})
    executor = LocalExecutor()
    params = {"action": "save", "content": "Alex prefers morning meetings."}
    pending = executor._check_approval_gate(tool, params)
    assert pending["_pending_approval"]
    tool._save.assert_not_awaited()
    assert params["content"] in pending["result"]
    approval_store.resolve(pending["approval_token"], "approved")
    assert executor._check_approval_gate(tool, params) is None
    assert (await tool.execute(**params))["success"]
    tool._save.assert_awaited_once_with("Alex prefers morning meetings.")


def test_declining_a_memory_change_does_not_authorize_it():
    tool = ManageUserMemory()
    executor = LocalExecutor()
    params = {"action": "forget", "content": "Alex prefers morning meetings."}
    pending = executor._check_approval_gate(tool, params)
    approval_store.resolve(pending["approval_token"], "declined")
    assert executor._check_approval_gate(tool, params)["_approval_declined"]
    assert "_approval_decision" not in params


@pytest.mark.parametrize("action", ["search", "status"])
def test_read_only_memory_does_not_need_consent(action):
    assert not ManageUserMemory().wants_approval({"action": action})


def test_telegram_cannot_bypass_memory_write_consent():
    turn_category_var.set("telegram_agent")
    result = LocalExecutor()._check_approval_gate(ManageUserMemory(), {"action": "save", "content": "A fact"})
    assert result["success"] is False
    assert "desktop" in result["error"]


def test_approval_for_one_fact_does_not_authorize_another_fact():
    tool = ManageUserMemory()
    executor = LocalExecutor()
    original = {"action": "save", "content": "Alex prefers morning meetings."}
    pending = executor._check_approval_gate(tool, original)
    approval_store.resolve(pending["approval_token"], "approved")
    changed = {"action": "save", "content": "A different, unreviewed fact."}
    assert executor._check_approval_gate(tool, changed)["success"] is False
    assert "_approval_decision" not in changed
    assert executor._check_approval_gate(tool, original) is None
