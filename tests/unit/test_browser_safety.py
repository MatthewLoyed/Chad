"""
Computer-use safeguards (issue #279): write-action classification, per-site
allowlist stricter treatment, and the kill-switch action budget, plus the
MCPExecutor integration that pauses browser writes through the same
executor-agnostic ApprovalCard infra as send_email/execute_sql/etc.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration import approval_store, browser_safety
from src.core.orchestration.context import session_id_var
from src.infrastructure.tools.execution.executor_strategy import MCPExecutor


class TestClassification:
    def test_always_write_tools_are_writes(self):
        for tool_name in ["browser_file_upload", "browser_handle_dialog", "browser_select_option", "browser_drag", "browser_fill_form", "browser_evaluate"]:
            assert browser_safety.is_write_action(tool_name, {}) is True

    def test_read_only_tools_are_never_writes(self):
        for tool_name in ["browser_navigate", "browser_snapshot", "browser_take_screenshot", "browser_tabs", "browser_wait_for"]:
            assert browser_safety.is_write_action(tool_name, {"url": "https://example.com"}) is False

    def test_click_matches_write_keyword(self):
        assert browser_safety.is_write_action("browser_click", {"element": "Submit button"}) is True
        assert browser_safety.is_write_action("browser_click", {"element": "Next page link"}) is False

    def test_type_with_submit_flag_is_write(self):
        assert browser_safety.is_write_action("browser_type", {"element": "Search box", "text": "hello", "submit": True}) is True
        assert browser_safety.is_write_action("browser_type", {"element": "Search box", "text": "hello"}) is False

    def test_type_matching_keyword_is_write(self):
        assert browser_safety.is_write_action("browser_type", {"element": "Email field", "text": "join our newsletter"}) is True

    def test_press_key_enter_is_write(self):
        assert browser_safety.is_write_action("browser_press_key", {"key": "Enter"}) is True
        assert browser_safety.is_write_action("browser_press_key", {"key": "PageDown"}) is False


class TestDomainAllowlist:
    def test_extract_domain_strips_www_and_scheme(self):
        assert browser_safety.extract_domain("https://www.supabase.com/dashboard") == "supabase.com"
        assert browser_safety.extract_domain("supabase.com/foo") == "supabase.com"
        assert browser_safety.extract_domain(None) is None
        assert browser_safety.extract_domain("") is None

    def test_allowlist_matches_subdomains(self):
        allowlist = ["supabase.com"]
        assert browser_safety.is_domain_allowlisted("supabase.com", allowlist) is True
        assert browser_safety.is_domain_allowlisted("app.supabase.com", allowlist) is True
        assert browser_safety.is_domain_allowlisted("notsupabase.com", allowlist) is False
        assert browser_safety.is_domain_allowlisted(None, allowlist) is False


class TestClassifyOffAllowlistStrictness:
    # These tests exercise the issue #279 "model_decide" gating behavior,
    # which is not the shipped default (DEFAULT_BROWSER_APPROVAL_MODE is
    # "allow_all"). Force the mode explicitly so results don't depend on
    # whatever tools.browser_approval_mode happens to be in the developer's
    # own gitignored config/preferences.yaml.
    def setup_method(self):
        self._patch = pytest.MonkeyPatch()
        self._patch.setattr(browser_safety, "get_browser_approval_mode", lambda: "model_decide")

    def teardown_method(self):
        self._patch.undo()

    def test_write_always_requires_approval_regardless_of_domain(self):
        verdict = browser_safety.classify("browser_click", {"element": "Submit"}, current_domain="supabase.com")
        assert verdict["is_write"] is True
        assert verdict["requires_approval"] is True

    def test_benign_click_on_allowlisted_domain_is_auto(self):
        verdict = browser_safety.classify("browser_click", {"element": "Next page"}, current_domain="supabase.com")
        assert verdict["requires_approval"] is False

    def test_benign_click_off_allowlist_requires_approval(self):
        verdict = browser_safety.classify("browser_click", {"element": "Next page"}, current_domain="sketchy-site.example")
        assert verdict["is_write"] is False
        assert verdict["requires_approval"] is True

    def test_read_only_tool_never_gated_even_off_allowlist(self):
        verdict = browser_safety.classify("browser_snapshot", {}, current_domain="sketchy-site.example")
        assert verdict["requires_approval"] is False


class TestBrowserSafetyState:
    def test_note_navigation_and_current_domain(self):
        state = browser_safety.BrowserSafetyState()
        state.note_navigation("conv-1", "https://www.github.com/foo")
        assert state.current_domain("conv-1") == "github.com"
        assert state.current_domain("conv-2") is None

    def test_action_budget_kill_switch(self, monkeypatch):
        monkeypatch.setattr(browser_safety, "get_browser_max_actions", lambda: 2)
        state = browser_safety.BrowserSafetyState()
        assert state.record_action("conv-1") == 1
        assert state.budget_exceeded("conv-1") is False
        assert state.record_action("conv-1") == 2
        assert state.budget_exceeded("conv-1") is False
        assert state.record_action("conv-1") == 3
        assert state.budget_exceeded("conv-1") is True
        # Independent per conversation.
        assert state.budget_exceeded("conv-2") is False


@pytest.mark.asyncio
class TestMCPExecutorBrowserGate:
    def setup_method(self):
        approval_store._pending.clear()
        self.token = session_id_var.set("conv-browser-test")
        # These tests assert issue #279 "model_decide" gating (writes pause
        # for approval), not the shipped "allow_all" default — force it so
        # results don't depend on the developer's local preferences.yaml.
        self._patch = pytest.MonkeyPatch()
        self._patch.setattr(browser_safety, "get_browser_approval_mode", lambda: "model_decide")

    def teardown_method(self):
        session_id_var.reset(self.token)
        self._patch.undo()

    async def test_read_only_navigate_runs_without_approval(self):
        class _FakeServer:
            name = "playwright"

            async def call_tool(self, name, params):
                return []

        tool = {"mcp_server": _FakeServer(), "name": "browser_navigate"}
        result = await MCPExecutor().execute(tool, {"url": "https://example.com"})
        assert "_pending_approval" not in result

    async def test_submit_click_pauses_for_approval(self):
        class _FakeServer:
            name = "playwright"

            async def call_tool(self, name, params):  # pragma: no cover - must NOT run
                raise AssertionError("write action executed without approval")

        tool = {"mcp_server": _FakeServer(), "name": "browser_click"}
        result = await MCPExecutor().execute(tool, {"element": "Submit order button", "target": "e3"})
        assert result["_pending_approval"] is True

    async def test_approved_submit_click_then_runs(self):
        executed = {"called": False}

        class _FakeServer:
            name = "playwright"

            async def call_tool(self, name, params):
                executed["called"] = True
                return []

        executor = MCPExecutor()
        tool = {"mcp_server": _FakeServer(), "name": "browser_click"}
        pending = await executor.execute(tool, {"element": "Submit order button", "target": "e3"})
        approval_store.resolve(pending["approval_token"], "approved")

        result = await executor.execute(tool, {"element": "Submit order button", "target": "e3"})
        assert executed["called"] is True
        assert "_pending_approval" not in result

    async def test_kill_switch_blocks_after_budget(self, monkeypatch):
        monkeypatch.setattr(browser_safety, "get_browser_max_actions", lambda: 1)

        class _FakeServer:
            name = "playwright"

            async def call_tool(self, name, params):
                return []

        executor = MCPExecutor()
        tool = {"mcp_server": _FakeServer(), "name": "browser_snapshot"}
        first = await executor.execute(tool, {})
        assert first["success"] is True

        second = await executor.execute(tool, {})
        assert second["success"] is False
        assert "kill-switch" in second["error"]
