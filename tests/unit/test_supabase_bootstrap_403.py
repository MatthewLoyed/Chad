"""Unit tests for optional Supabase upgrade (issue #403)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.services import supabase_bootstrap


class TestSupabaseBootstrapEnv:
    def test_write_supabase_env_updates_existing_keys(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GEMINI_API_KEY=old\nSUPABASE_URL=https://old.supabase.co\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(supabase_bootstrap, "_env_path", lambda: env_file)

        supabase_bootstrap.write_supabase_env(
            url="https://new.supabase.co",
            anon_key="anon-new",
            service_key="service-new",
            db_url="postgresql://new",
        )

        content = env_file.read_text(encoding="utf-8")
        assert "GEMINI_API_KEY=old" in content
        assert "SUPABASE_URL=https://new.supabase.co" in content
        assert "SUPABASE_ANON_KEY=anon-new" in content
        assert "SUPABASE_SERVICE_KEY=service-new" in content
        assert "SUPABASE_DB_URL=postgresql://new" in content

    def test_get_supabase_settings_masks_secrets(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "sb_publishable_abcdefghijklmnop")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_abcdefghijklmnop")
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://postgres.abc:secret@host/db")

        with patch.object(supabase_bootstrap, "_env_path", return_value=MagicMock(exists=lambda: False)):
            settings = supabase_bootstrap.get_supabase_settings()

        assert settings["configured"] is True
        assert settings["url"] == "https://abc.supabase.co"
        assert "…" in settings["anon_key"]
        assert "abcdefghijklmnop" not in settings["anon_key"]


class TestRefreshRagMcpFiltering:
    def test_mcp_servers_for_rag_scan_uses_effective_servers(self):
        effective = {"brave-search": {"command": ["npx"], "env": {"BRAVE_API_KEY": "real-key"}}}
        with patch(
            "src.scripts.refresh_rag_tools.get_effective_mcp_servers",
            return_value=effective,
        ):
            from src.scripts.refresh_rag_tools import mcp_servers_for_rag_scan

            assert mcp_servers_for_rag_scan() == effective

    @pytest.mark.asyncio
    async def test_refresh_aborts_only_on_credentialed_scan_failures(self):
        """Token-less MCPs are skipped; only credentialed scan failures abort."""
        from src.scripts import refresh_rag_tools

        effective = {
            "good-server": {"command": ["cmd"], "env": {"TOKEN": "ok"}},
        }
        registry = MagicMock()
        registry.get_tool_count.return_value = 1
        registry._tools = {"local_tool": MagicMock()}

        failing_server = MagicMock()
        failing_server.name = "good-server"

        mcp_registry = MagicMock()
        mcp_registry._servers.values.return_value = [failing_server]

        sync_bridge = MagicMock()
        sync_bridge.sync_all = AsyncMock()

        async def _fail_scan(*_args, **_kwargs):
            raise RuntimeError("connection refused")

        with (
            patch.object(refresh_rag_tools, "ToolRegistry", return_value=registry),
            patch.object(refresh_rag_tools, "mcp_servers_for_rag_scan", return_value=effective),
            patch.object(refresh_rag_tools, "MCPRegistry", return_value=mcp_registry),
            patch(
                "src.infrastructure.tools.discovery.mcp_scanner.MCPScanner",
            ) as scanner_cls,
            patch.object(refresh_rag_tools, "SupabaseToolSyncBridge", return_value=sync_bridge),
        ):
            scanner_cls.return_value.scan_server = _fail_scan
            await refresh_rag_tools.refresh_supabase_tools()

        sync_bridge.sync_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_skips_tokenless_servers_without_abort(self):
        from src.scripts import refresh_rag_tools

        registry = MagicMock()
        registry.get_tool_count.return_value = 2
        registry._tools = {"a": MagicMock(), "b": MagicMock()}

        mcp_registry = MagicMock()
        mcp_registry._servers.values.return_value = []

        sync_bridge = MagicMock()
        sync_bridge.sync_all = AsyncMock()

        with (
            patch.object(refresh_rag_tools, "ToolRegistry", return_value=registry),
            patch.object(refresh_rag_tools, "mcp_servers_for_rag_scan", return_value={}),
            patch.object(refresh_rag_tools, "ConfigManager") as cfg_mgr,
            patch.object(refresh_rag_tools, "MCPRegistry", return_value=mcp_registry),
            patch.object(refresh_rag_tools, "SupabaseToolSyncBridge", return_value=sync_bridge),
            patch(
                "src.infrastructure.tools.selection.semantic_selector.SemanticToolSelector",
            ) as selector_cls,
            patch(
                "src.infrastructure.database.supabase_manager.get_supabase_manager",
                return_value=MagicMock(enabled=False),
            ),
        ):
            selector_cls.return_value._model = MagicMock()
            cfg_mgr.get.return_value = {
                "needs-token": {"command": ["npx"], "env": {"API_KEY": "%API_KEY%"}},
            }
            await refresh_rag_tools.refresh_supabase_tools()

        sync_bridge.sync_all.assert_called_once()
