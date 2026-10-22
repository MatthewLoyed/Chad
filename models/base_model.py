"""
Base Model Abstract Class
Defines the interface that all AI models must implement for the voice assistant.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, AsyncGenerator, Optional, List, Callable, TYPE_CHECKING
from src.shared.config_manager import ConfigManager

if TYPE_CHECKING:
    from src.core.intelligence.adapters.ai_adapter_base import GenerateResponse


class BaseModel(ABC):
    """
    Abstract base class for all AI model implementations.
    Any LLM integration (Gemini, OpenAI, etc.) must implement this interface.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the model with configuration.
        
        Args:
            config: Model-specific configuration dictionary
        """
        self.config = config
        self.model_name = config.get('model')
        if not self.model_name or self.model_name == "default":
            self.model_name = ConfigManager.get_active_model()
        self.temperature = config.get('temperature', 0.7)
    
    @abstractmethod
    async def stream_audio(
        self, 
        audio_input: AsyncGenerator[bytes, None],
        tool_schemas: list[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream audio input to the model and receive audio output + tool calls.
        
        Args:
            audio_input: Async generator yielding raw audio bytes (16-bit PCM, 16kHz, mono)
            tool_schemas: List of tool schemas available to the model
            
        Yields:
            Dictionary with one of:
                - {'type': 'audio', 'data': bytes} - Audio response from model
                - {'type': 'tool_call', 'name': str, 'id': str, 'params': dict} - Tool call request
                - {'type': 'turn_complete'} - Conversation turn finished
                - {'type': 'error', 'message': str} - Error occurred
        """
        pass
    
    @abstractmethod
    async def execute_text(
        self, 
        prompt: str,
        tool_schemas: Optional[list[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Execute a text-only interaction (for tools that don't need voice).
        
        Args:
            prompt: Text prompt to send to the model
            tool_schemas: Optional list of tool schemas
            
        Returns:
            Dictionary with response or tool call
        """
        pass
    
    @abstractmethod
    def get_capabilities(self) -> Dict[str, bool]:
        """
        Return what this model can do.
        
        Returns:
            Dictionary of capabilities, e.g.:
            {
                'audio_input': True,
                'audio_output': True,
                'vision': False,
                'function_calling': True,
                'streaming': True
            }
        """
        pass
    
    @abstractmethod
    async def send_tool_response(self, tool_call_id: str, result: Dict[str, Any]) -> None:
        """
        Send tool execution result back to the model.
        
        Args:
            tool_call_id: ID of the tool call this responds to
            result: Tool execution result
        """
        pass
    
    @abstractmethod
    async def interrupt(self) -> None:
        """
        Interrupt the model's current output (user is speaking over it).
        """
        pass
    
    async def send_turn_complete(self) -> None:
        """
        Signal that the user has finished speaking for this turn.
        Override if model supports manual turn completion (e.g. Gemini Live).
        """
        pass
    
    async def cleanup(self) -> None:
        """
        Clean up resources (connections, streams, etc.).
        Default implementation does nothing; override if needed.
        """
        pass

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> 'GenerateResponse':
        """
        Unified text generation interface.
        Must be implemented by models supporting the ReAct orchestrator.
        """
        raise NotImplementedError(f"Model {self.__class__.__name__} does not support unified text generation.")

    def convert_tool_schemas(self, schemas: List[Dict[str, Any]]) -> List[Any]:
        """
        Convert tool schemas to model-specific format.
        """
        return schemas

    def build_tool_result_message(self, tool_call_id: str, name: str, result: Any) -> Dict[str, Any]:
        """
        Build a message for tool results.
        """
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": str(result)}

    def build_assistant_tool_call_message(self, tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Build a message for assistant tool calls.
        """
        return {"role": "assistant", "tool_calls": tool_calls}


