"""Unit tests for connector opt-in policy."""

import os
import pytest

from src.infrastructure.connectors.policy import (
    mcp_credentials_satisfied,
    ensure_connector_defaults,
    get_effective_mcp_servers,
    normalize_mcp_env_for_storage,
)


def test_keyless_mcp_always_satisfied():
    ok, missing = mcp_credentials_satisfied({"env": {}})
    assert ok is True
    assert missing == []


def test_missing_api_key_not_satisfied(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    ok, missing = mcp_credentials_satisfied({
        "env": {"BRAVE_API_KEY": "%BRAVE_API_KEY%"},
    })
    assert ok is False
    assert "BRAVE_API_KEY" in missing


def test_resolved_api_key_satisfied(monkeypatch):
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_test_token")
    ok, missing = mcp_credentials_satisfied({
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "%GITHUB_PERSONAL_ACCESS_TOKEN%"},
    })
    assert ok is True
    assert missing == []


def test_ensure_defaults_disables_shipped_mcps(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    prefs = ensure_connector_defaults({})
    assert prefs.get("connectors_opt_in_v1") is True
    disabled = set(prefs.get("disabled_mcp_servers", []))
    # github needs a token that isn't set — stays disabled until configured.
    assert "github" in disabled
    # playwright ships with no required env ("env: {}" in config.yaml), so the
    # credential-based opt-in re-enables it immediately — it is NOT kept off by
    # this mechanism. (Browser-automation safety is instead handled by
    # disabled_subagents gating browser_agent itself — see config.yaml #219/#402.)


def test_shipped_mcp_includes_oauth_connectors():
    from src.infrastructure.connectors.policy import SHIPPED_MCP_SERVER_IDS

    # Wizard-only OAuth MCPs (not always in config.yaml) still need shipped IDs so
    # call_mcp_tool can say "connect in Settings" instead of "unknown server".
    assert "canva" in SHIPPED_MCP_SERVER_IDS
    assert "supabase" in SHIPPED_MCP_SERVER_IDS
    assert "excalidraw" in SHIPPED_MCP_SERVER_IDS


def test_normalize_env_uses_placeholders():
    stored = normalize_mcp_env_for_storage(
        {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_secret"},
        {"GITHUB_PERSONAL_ACCESS_TOKEN": "%GITHUB_PERSONAL_ACCESS_TOKEN%"},
    )
    assert stored["GITHUB_PERSONAL_ACCESS_TOKEN"] == "%GITHUB_PERSONAL_ACCESS_TOKEN%"


def test_effective_servers_empty_when_all_disabled(monkeypatch):
    # Isolate from whatever the developer's own config/config.yaml happens to
    # list under mcp_servers (e.g. a locally-added "supabase" entry) — this
    # test asserts policy behavior, not the local machine's server list.
    from src.infrastructure.connectors import policy as policy_mod

    monkeypatch.setattr(policy_mod.ConfigManager, "get", lambda *a, **k: {})

    prefs = {
        "connectors_opt_in_v1": True,
        "disabled_mcp_servers": [
            "playwright", "brave-search", "todoist", "fetch",
            "github", "google-drive", "notion", "excalidraw",
        ],
    }
    effective = get_effective_mcp_servers(prefs)
    assert effective == {}
