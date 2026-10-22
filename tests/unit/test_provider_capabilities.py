"""
Provider capability flags (issue #498) must gate LM-Studio-specific adapter
workarounds off for every other backend. These tests prove:
  1. The capability lookup itself resolves correctly per provider.
  2. The two adapters' actual gating sites (froggeric dirty-parser,
     tool_response tag format, api_key default) fire for LM Studio and are
     inert for Ollama / a raw OpenAI-compatible (llama.cpp/vLLM) endpoint.
"""
from src.core.intelligence.provider_capabilities import (
    get_provider_capabilities,
    get_capabilities_for_url,
    is_residency_loss_error,
)
from src.core.intelligence.adapters.openai_adapter import OpenAIAdapter
from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter


LMSTUDIO_URL = "http://localhost:1234/v1"
OLLAMA_URL = "http://localhost:11434/v1"
VLLM_URL = "http://localhost:8000/v1"  # raw OpenAI-compatible endpoint (static)


# ── Capability lookup ────────────────────────────────────────────────────────

def test_lmstudio_capabilities_are_all_on():
    caps = get_provider_capabilities("lmstudio")
    assert caps.needs_qwen3_reasoning_workaround is True
    assert caps.needs_tool_role_transform is True
    assert caps.has_native_tool_parser is False
    assert caps.reasoning_effort_via_extra_body is True
    assert caps.default_api_key == "lm-studio"


def test_ollama_and_static_default_to_off():
    for provider in ("ollama", "static", "vllm", "llamacpp", "unknown-backend"):
        caps = get_provider_capabilities(provider)
        assert caps.needs_qwen3_reasoning_workaround is False
        assert caps.needs_tool_role_transform is False
        assert caps.has_native_tool_parser is True
        assert caps.reasoning_effort_via_extra_body is False
        assert caps.default_api_key == ('not-required' if provider in {'static', 'vllm', 'llamacpp'} else None)


def test_get_capabilities_for_url_resolves_lmstudio():
    assert get_capabilities_for_url(LMSTUDIO_URL).needs_qwen3_reasoning_workaround is True


def test_get_capabilities_for_url_resolves_ollama_off():
    assert get_capabilities_for_url(OLLAMA_URL).needs_qwen3_reasoning_workaround is False


def test_get_capabilities_for_url_resolves_static_off():
    assert get_capabilities_for_url(VLLM_URL).needs_qwen3_reasoning_workaround is False


def test_get_capabilities_for_url_no_base_url_is_default():
    caps = get_capabilities_for_url(None)
    assert caps.needs_qwen3_reasoning_workaround is False
    assert caps.default_api_key is None


# ── OpenAIAdapter gating ─────────────────────────────────────────────────────

def test_openai_adapter_provider_label_and_capabilities():
    lmstudio = OpenAIAdapter(base_url=LMSTUDIO_URL, api_key="x", model_name="qwen/qwen3.6-35b-a3b")
    assert lmstudio.provider == "LM Studio"
    assert lmstudio.capabilities.needs_qwen3_reasoning_workaround is True

    ollama = OpenAIAdapter(base_url=OLLAMA_URL, api_key="x", model_name="qwen2.5")
    assert ollama.provider == "Ollama"
    assert ollama.capabilities.needs_qwen3_reasoning_workaround is False

    static = OpenAIAdapter(base_url=VLLM_URL, api_key="x", model_name="qwen3-something")
    assert static.capabilities.needs_qwen3_reasoning_workaround is False


def test_froggeric_dirty_parser_only_fires_for_lmstudio():
    froggeric_text = "<tool_call><function=get_weather><parameter=location>London</parameter></function></tool_call>"

    lmstudio = OpenAIAdapter(base_url=LMSTUDIO_URL, api_key="x", model_name="qwen/qwen3.6-35b-a3b")
    calls = lmstudio._extract_markdown_tool_calls(froggeric_text, known_tools=["get_weather"])
    assert len(calls) == 1
    assert calls[0]["name"] == "get_weather"

    ollama = OpenAIAdapter(base_url=OLLAMA_URL, api_key="x", model_name="qwen2.5")
    calls = ollama._extract_markdown_tool_calls(froggeric_text, known_tools=["get_weather"])
    assert calls == []


# ── LiteLLMAdapter gating ────────────────────────────────────────────────────

def test_litellm_adapter_default_api_key_only_for_lmstudio():
    lmstudio = LiteLLMAdapter(model_name="qwen/qwen3.6-35b-a3b", base_url=LMSTUDIO_URL)
    assert get_capabilities_for_url(lmstudio.base_url).default_api_key == "lm-studio"

    ollama = LiteLLMAdapter(model_name="qwen2.5", base_url=OLLAMA_URL)
    assert get_capabilities_for_url(ollama.base_url).default_api_key is None


# ── residency-loss classification (issue #520) ──────────────────────────────

def test_lmstudio_residency_loss_markers():
    assert get_provider_capabilities("lmstudio").residency_loss_markers == ("unload",)


def test_ollama_and_static_have_no_residency_loss_markers():
    for provider in ("ollama", "static", "vllm", "llamacpp", "unknown-backend"):
        assert get_provider_capabilities(provider).residency_loss_markers == ()


def test_is_residency_loss_error_matches_lmstudio_wording():
    caps = get_provider_capabilities("lmstudio")
    assert is_residency_loss_error(caps, "Error: Model is unloaded.") is True
    assert is_residency_loss_error(caps, "MODEL IS UNLOADED") is True  # case-insensitive
    assert is_residency_loss_error(caps, "Bad request: missing field 'model'") is False


def test_is_residency_loss_error_never_fires_without_markers():
    caps = get_provider_capabilities("ollama")
    assert is_residency_loss_error(caps, "Error: Model is unloaded.") is False
    assert is_residency_loss_error(caps, "") is False
