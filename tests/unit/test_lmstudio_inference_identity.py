"""Exercise real LiteLLM provider stripping, not just adapter argument mocks."""
from unittest.mock import AsyncMock

import pytest
from litellm.litellm_core_utils.get_llm_provider_logic import get_llm_provider

from src.core.intelligence.endpoint_contract import inference_model_name
from src.core.intelligence.providers import LMStudioLifecycle


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,native", [
    ("openai/gpt-oss-20b", "openai/gpt-oss-20b"),
    ("openai/openai/gpt-oss-20b", "openai/gpt-oss-20b"),
    ("openai/google/gemma-example", "google/gemma-example"),
    ("qwen/example", "qwen/example"),
])
async def test_inference_preserves_the_catalog_key_after_real_litellm_routing(
    monkeypatch, configured, native
):
    provider = LMStudioLifecycle("http://localhost:1234/v1")
    monkeypatch.setattr(provider, "_native_models", AsyncMock(return_value=[
        {"key": native, "loaded_instances": [{"id": native}]},
    ]))
    monkeypatch.setattr("src.core.intelligence.providers.get_provider_for", lambda _: provider)
    routed = await inference_model_name(configured, provider.base_url)
    wire_model, backend, _, _ = get_llm_provider(routed, api_base=provider.base_url)
    assert backend == "openai"
    assert wire_model == native
