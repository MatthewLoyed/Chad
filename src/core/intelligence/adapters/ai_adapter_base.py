"""
AI Adapter Base Interface - Standardizing model communication.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable


@dataclass
class TokenUsage:
    """Granular token consumption metrics."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: Optional[int] = None
    context_window: Optional[int] = None


@dataclass
class GenerateResponse:
    """Normalized response from any AI model."""
    text: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]  # Each: {id, name, args: dict}
    finish_reason: str  # "stop" | "tool_calls" | "length" | "error"
    reasoning: Optional[str] = None
    raw: Any = None
    usage: Optional[TokenUsage] = None


class AIAdapter(ABC):
    """
    Abstract base class for all AI model adapters.
    """

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Callable[..., Any]] = None,
        on_token: Optional[Callable[..., Any]] = None,
        **kwargs
    ) -> GenerateResponse:
        """
        Send a request to the AI model.
        """
        pass

    @abstractmethod
    def convert_tool_schemas(self, schemas: List[Dict[str, Any]]) -> List[Any]:
        """
        Convert Chad's internal schemas to the model's expected format.
        """
        pass

    @abstractmethod
    def build_tool_result_message(
        self,
        tool_call_id: str,
        name: str,
        result: Any
    ) -> Dict[str, Any]:
        """
        Build a message containing a tool result for the conversation history.
        """
        pass

    @abstractmethod
    def build_assistant_tool_call_message(
        self,
        tool_calls: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Build an assistant message that contains tool calls.
        """
        pass

    @abstractmethod
    async def is_ready(self) -> bool:
        """
        Check if the model/server is reachable and ready to process requests.
        Returns:
            True if ready, False otherwise.
        """
        pass

    async def wait_until_ready(self, timeout: float = 10.0) -> bool:
        """
        Poll is_ready until it returns True or timeout is reached.
        """
        import asyncio
        import time
        start_time = time.time()
        while time.time() - start_time < timeout:
            if await self.is_ready():
                return True
            await asyncio.sleep(0.5)
        return False
