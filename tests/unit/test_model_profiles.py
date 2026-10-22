"""
Smoke tests for the model registry and profile-driven adapter behaviour.

Run with: python -m pytest tests/unit/test_model_profiles.py -v
These tests are offline — no LM Studio connection required.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from config.model_profiles import ModelProfile
from config.model_registry import MODELS, get_profile


# ── Registry lookup ───────────────────────────────────────────────────────────

def test_all_registry_entries_are_model_profiles():
    for key, value in MODELS.items():
        assert isinstance(value, ModelProfile), f"Entry '{key}' is not a ModelProfile"


def test_exact_lookup():
    p = get_profile("qwen/qwen3.6-35b-a3b")
    assert p is not None
    assert p.model_id == "qwen/qwen3.6-35b-a3b"


def test_versioned_suffix_stripped():
    """LM Studio appends ':2', ':3', etc. — get_profile must strip them."""
    p = get_profile("qwen/qwen3.6-35b-a3b:2")
    assert p is not None
    assert p.model_id == "qwen/qwen3.6-35b-a3b"


def test_unknown_model_returns_none():
    assert get_profile("some/unknown-model-xyz") is None


def test_gemma4_in_registry():
    p = get_profile("google/gemma-4-31b-it-qat-q4_0")
    assert p is not None
    assert p.thinking_mode is True
    assert p.thinking_param_style is None  # no API toggle for Gemma 4


# ── Profile field contracts ───────────────────────────────────────────────────

def test_qwen3_profile_has_qwen3_style():
    p = get_profile("qwen/qwen3.6-35b-a3b")
    assert p.thinking_mode is True
    assert p.thinking_param_style == "qwen3"
    assert p.thinking_budget > 0
    assert p.thinking_effort in ("minimal", "medium", "high")


def test_all_profiles_have_positive_context_length():
    for key, p in MODELS.items():
        assert p.context_length > 0, f"{key}: context_length must be positive"


def test_all_profiles_have_valid_temperature():
    for key, p in MODELS.items():
        assert 0.0 <= p.temperature <= 2.0, f"{key}: temperature out of range"


# ── Adapter thinking-param injection (offline, no LM Studio) ─────────────────

def _make_adapter(model_id: str):
    """Build a LiteLLMAdapter with the registry profile, no real connection."""
    from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
    profile = get_profile(model_id)
    return LiteLLMAdapter(
        model_name=model_id,
        api_key="test-key",
        base_url="http://localhost:1234/v1",
        profile=profile,
    )


def test_qwen3_adapter_injects_thinking_params():
    adapter = _make_adapter("qwen/qwen3.6-35b-a3b")
    req: dict = {"extra_body": {"keep_alive": -1}}
    adapter._apply_profile_thinking(req, {})
    assert "reasoning_effort" in req
    extra = req.get("extra_body", {})
    assert "thinking_budget" in extra
    assert "chat_template_kwargs" in extra


def test_gemma4_adapter_does_not_inject_thinking_params():
    adapter = _make_adapter("google/gemma-4-31b-it-qat-q4_0")
    req: dict = {"extra_body": {"keep_alive": -1}}
    adapter._apply_profile_thinking(req, {})
    # No qwen3-style params should be added
    assert "reasoning_effort" not in req
    extra = req.get("extra_body", {})
    assert "chat_template_kwargs" not in extra


def test_thinking_disabled_per_call():
    adapter = _make_adapter("qwen/qwen3.6-35b-a3b")
    req: dict = {}
    adapter._apply_profile_thinking(req, {"thinking_enabled": False})
    assert req.get("reasoning_effort") == "none"
    assert req["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert req["extra_body"]["thinking_budget"] == 0


def test_thinking_level_override():
    adapter = _make_adapter("qwen/qwen3.6-35b-a3b")
    req: dict = {}
    adapter._apply_profile_thinking(req, {"thinking_level": "minimal"})
    extra = req.get("extra_body", {})
    # "minimal" level should set enable_thinking=False
    assert extra.get("chat_template_kwargs", {}).get("enable_thinking") is False
    assert extra.get("thinking_budget") == 8192
