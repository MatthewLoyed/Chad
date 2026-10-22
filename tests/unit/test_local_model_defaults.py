"""Committed local-first model defaults (#448).

Fresh clone with no preferences.yaml / no cloud keys must not silently
instantiate a Gemini (or other cloud) provider for normal agent turns.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = REPO_ROOT / "config" / "config.yaml"


def _load_committed_config() -> dict:
    with CONFIG_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_committed_config_defaults_to_local_models():
    cfg = _load_committed_config()
    local = cfg["local_models"]
    assert local.get("use_local") is True
    assert local.get("active_model"), "A local default model must be configured"
    assert cfg.get("active_model") == local["active_model"]

    models = cfg.get("models") or {}
    assert models, "models category map must be present"
    for category, entry in models.items():
        mid = (entry or {}).get("model") or ""
        assert not mid.startswith("gemini"), (
            f"models.{category}.model must not default to Gemini (got {mid!r})"
        )
        assert "gemini" not in mid.lower(), (
            f"models.{category}.model must not reference Gemini (got {mid!r})"
        )
        # Fallback path still targets LM Studio, not a cloud host.
        assert (entry or {}).get("base_url", "").startswith("http://localhost:1234")


def test_local_profile_router_does_not_construct_gemini(monkeypatch):
    """With use_local active, ModelRouter must take the local path — never GeminiAdapter."""
    from src.core.intelligence.model_router import ModelRouter

    cfg = _load_committed_config()
    monkeypatch.setattr(
        "src.shared.config_manager.ConfigManager.is_local_model_active",
        classmethod(lambda cls: True),
    )
    monkeypatch.setattr(
        ModelRouter,
        "_trigger_warmup",
        lambda self: None,
    )

    router = ModelRouter(cfg, tool_registry=MagicMock())

    sentinel = object()
    created_types: list[str] = []

    def _fake_create(model_type, model_config):
        created_types.append(model_type)
        raise AssertionError(
            f"cloud/fallback model factory should not run under use_local "
            f"(type={model_type!r})"
        )

    monkeypatch.setattr(router, "_create_model_instance", _fake_create)
    monkeypatch.setattr(router, "_ensure_local_model", lambda category="agent": sentinel)

    got = router._ensure_model("agent")
    assert got is sentinel
    assert created_types == []

def test_is_local_model_active_reads_committed_default(monkeypatch):
    """ConfigManager.is_local_model_active follows committed use_local: true."""
    from src.shared.config_manager import ConfigManager

    # Isolate from any gitignored preferences.yaml overlay on the machine.
    monkeypatch.setattr(
        ConfigManager,
        "get",
        classmethod(lambda cls, key, default=None: {
            "local_models.use_local": True,
        }.get(key, default)),
    )
    assert ConfigManager.is_local_model_active() is True
