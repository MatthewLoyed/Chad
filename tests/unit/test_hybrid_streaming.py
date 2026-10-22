"""Unit tests for hybrid streaming mode selection (revert: CHAD_HYBRID_STREAMING=false)."""
import os
from unittest.mock import patch

import pytest

from src.core.intelligence.adapters.litellm_adapter import (
    LiteLLMAdapter,
    hybrid_streaming_enabled,
    requires_legacy_nonstreaming_structured,
)


@pytest.fixture
def adapter():
    return LiteLLMAdapter(
        model_name="openai/google/gemma-4-31b-it-qat-q4_0",
        base_url="http://localhost:1234/v1",
    )


def test_hybrid_streaming_default_on():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CHAD_HYBRID_STREAMING", None)
        assert hybrid_streaming_enabled() is True


def test_hybrid_streaming_can_disable():
    with patch.dict(os.environ, {"CHAD_HYBRID_STREAMING": "false"}):
        assert hybrid_streaming_enabled() is False


def test_qwen3_structured_requires_legacy():
    assert requires_legacy_nonstreaming_structured("openai/qwen/qwen3.6-35b-a3b") is True
    assert requires_legacy_nonstreaming_structured("google/gemma-4-31b") is False


def test_hybrid_streams_tool_turns(adapter):
    assert adapter._resolve_use_streaming(has_tools=True, has_response_format=False) is True


def test_hybrid_streams_gemma_schema_turns(adapter):
    assert adapter._resolve_use_streaming(has_tools=False, has_response_format=True) is True


def test_qwen3_schema_stays_non_streaming():
    qwen = LiteLLMAdapter(
        model_name="openai/qwen/qwen3.6-35b-a3b",
        base_url="http://localhost:1234/v1",
    )
    assert qwen._resolve_use_streaming(has_tools=False, has_response_format=True) is False


def test_legacy_flag_disables_tool_streaming(adapter):
    with patch.dict(os.environ, {"CHAD_HYBRID_STREAMING": "false"}):
        assert adapter._resolve_use_streaming(has_tools=True, has_response_format=False) is False


def test_gemma_emits_reasoning_stream(adapter):
    assert adapter._model_emits_reasoning_stream() is True


# ── Inline <think> tag parsing ───────────────────────────────────────────────

def test_split_inline_thinking_simple():
    content, thinking, in_think = LiteLLMAdapter._split_inline_thinking(
        "<think>I reason here</think>actual answer", False
    )
    assert content == "actual answer"
    assert thinking == "I reason here"
    assert in_think is False


def test_split_inline_thinking_no_tags():
    content, thinking, in_think = LiteLLMAdapter._split_inline_thinking("hello world", False)
    assert content == "hello world"
    assert thinking == ""
    assert in_think is False


def test_split_inline_thinking_open_tag_spans_chunks():
    # First chunk opens <think> but doesn't close it
    content1, thinking1, in_think = LiteLLMAdapter._split_inline_thinking(
        "before<think>start of reasoning", False
    )
    assert content1 == "before"
    assert thinking1 == "start of reasoning"
    assert in_think is True

    # Second chunk continues inside think block
    content2, thinking2, in_think = LiteLLMAdapter._split_inline_thinking(
        "more reasoning</think>after", in_think
    )
    assert content2 == "after"
    assert thinking2 == "more reasoning"
    assert in_think is False


def test_split_inline_thinking_pure_think_chunk():
    # Chunk arrives entirely inside an open think block
    content, thinking, in_think = LiteLLMAdapter._split_inline_thinking(
        "still reasoning", True
    )
    assert content == ""
    assert thinking == "still reasoning"
    assert in_think is True


def test_strip_inline_thinking_from_content():
    cleaned, reasoning = LiteLLMAdapter._strip_inline_thinking_from_content(
        "<think>My reasoning</think>Final answer."
    )
    assert cleaned == "Final answer."
    assert reasoning == "My reasoning"


def test_strip_inline_thinking_multiple_blocks():
    cleaned, reasoning = LiteLLMAdapter._strip_inline_thinking_from_content(
        "<think>step 1</think>some text<think>step 2</think>end"
    )
    assert cleaned == "some textend"
    assert "step 1" in reasoning and "step 2" in reasoning


def test_strip_inline_thinking_no_tags():
    cleaned, reasoning = LiteLLMAdapter._strip_inline_thinking_from_content("Just content.")
    assert cleaned == "Just content."
    assert reasoning is None


def test_strip_inline_thinking_unclosed_tag():
    cleaned, reasoning = LiteLLMAdapter._strip_inline_thinking_from_content(
        "prefix<think>trailing reasoning with no close"
    )
    assert cleaned == "prefix" or cleaned is None  # trailing think stripped
    assert reasoning == "trailing reasoning with no close"
