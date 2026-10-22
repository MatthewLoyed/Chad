"""
LLM Infrastructure Layer

Port-Adapter pattern for LLM providers.
Abstracts LLM API calls to enable multi-provider support.
"""

from .llm_port import (
    LLMPort,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    LLMToolDefinition,
    LLMModelInfo,
    LLMRole
)
from .gemini_adapter import GeminiAdapter

__all__ = [
    'LLMPort',
    'LLMMessage',
    'LLMResponse',
    'LLMStreamChunk',
    'LLMToolDefinition',
    'LLMModelInfo',
    'LLMRole',
    'GeminiAdapter',
]
