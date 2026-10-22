"""
LLM Port Interface

Abstract interface for LLM providers using Port-Adapter pattern.
All LLM adapters (Gemini, Claude, GPT, etc.) implement this interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncIterator, Union
from enum import Enum


class LLMRole(str, Enum):
    """Message roles in conversation"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class LLMMessage:
    """Single message in conversation history"""
    role: LLMRole
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMToolDefinition:
    """Tool definition for function calling"""
    name: str
    description: str
    parameters: Dict[str, Any]
    required: List[str] = field(default_factory=list)


@dataclass
class LLMResponse:
    """Complete LLM response (non-streaming)"""
    content: str
    role: LLMRole = LLMRole.ASSISTANT
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMStreamChunk:
    """Single chunk in streaming response"""
    content: Optional[str] = None
    role: Optional[LLMRole] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    is_final: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMModelInfo:
    """Model capabilities and metadata"""
    provider: str
    model_name: str
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    max_tokens: int = 4096
    context_window: int = 128000
    metadata: Dict[str, Any] = field(default_factory=dict)


class LLMPort(ABC):
    """
    Abstract interface for LLM providers.

    All LLM adapters must implement this interface to ensure
    consistent behavior across different providers.
    """

    @abstractmethod
    async def generate(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[LLMToolDefinition]] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Generate a complete response (non-streaming).

        Args:
            messages: Conversation history
            tools: Available tools for function calling
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Provider-specific parameters

        Returns:
            Complete LLM response
        """
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[LLMToolDefinition]] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[LLMStreamChunk]:
        """
        Generate a streaming response.

        Args:
            messages: Conversation history
            tools: Available tools for function calling
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Provider-specific parameters

        Yields:
            Stream chunks as they arrive
        """
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """
        Count tokens in text using provider's tokenizer.

        Args:
            text: Text to tokenize

        Returns:
            Token count
        """
        pass

    @abstractmethod
    def get_model_info(self) -> LLMModelInfo:
        """
        Get model capabilities and metadata.

        Returns:
            Model information
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Clean up resources (connections, sessions, etc.).
        """
        pass

    # Optional: Can be overridden for provider-specific features

    def supports_function_calling(self) -> bool:
        """Check if provider supports function/tool calling"""
        return self.get_model_info().supports_tools

    def supports_vision(self) -> bool:
        """Check if provider supports vision/image inputs"""
        return self.get_model_info().supports_vision

    def validate_temperature(self, temp: float) -> float:
        """Validate and clamp temperature to valid range"""
        return max(0.0, min(2.0, temp))
