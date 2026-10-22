"""
LiteLLM Model Implementation
Standardized model wrapper using LiteLLMAdapter.
"""

from typing import Dict, Any, AsyncGenerator, Optional, List, Callable
import logging

from models.base_model import BaseModel
from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
from src.core.intelligence.adapters.ai_adapter_base import GenerateResponse
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("LiteLLMModel")

class LiteLLMModel(BaseModel):
    """
    BaseModel implementation that uses the LiteLLMAdapter.
    Supports any model provider via LiteLLM's unified interface.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Extract configuration
        model_name = config.get('model') or ConfigManager.get_active_model()
        api_key = config.get('api_key')
        base_url = config.get('base_url')
        system_instruction = config.get('system_instruction', ConfigManager.get_persona_instruction())
        supports_tools = config.get('supports_tools', False)
        
        # Look up model profile for capability-driven quirk handling
        from config.model_registry import get_profile
        profile = config.get('profile') or get_profile(model_name)
        if profile:
            supports_tools = profile.supports_native_tool_calls
            logger.info(f"[ LITELLM_MODEL ] Profile found for {model_name} (thinking={profile.thinking_mode}, style={profile.thinking_param_style})")

        # Initialize the unified LiteLLM adapter
        self.adapter = LiteLLMAdapter(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            system_instruction=system_instruction,
            supports_tools=supports_tools,
            profile=profile,
        )
        
        logger.info(f"[ LITELLM_MODEL ] Initialized {model_name}")

    async def stream_audio(
        self, 
        audio_input: AsyncGenerator[bytes, None],
        tool_schemas: List[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Standard LiteLLM does not support native audio streaming (like Gemini Live).
        This method remains for compatibility but yields an error/turn_complete.
        """
        logger.warning("[ LITELLM_MODEL ] Native audio streaming not supported via standard LiteLLM.")
        yield {'type': 'error', 'message': 'Native audio streaming not supported for this model type.'}
        yield {'type': 'turn_complete'}

    async def execute_text(
        self, 
        prompt: str,
        tool_schemas: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Execute a text interaction.
        """
        # Use adapter to generate
        response: GenerateResponse = await self.adapter.generate(
            messages=[{"role": "user", "content": prompt}],
            tools=tool_schemas,
            temperature=self.temperature
        )
        
        # Convert response to BaseModel format
        result = {
            'text': response.text,
            'tool_calls': response.tool_calls,
            'reasoning': response.reasoning,
            'finish_reason': response.finish_reason
        }
        return result

    def get_capabilities(self) -> Dict[str, bool]:
        return {
            'audio_input': False,
            'audio_output': False,
            'vision': True,  # LiteLLM supports vision for many models
            'function_calling': True,
            'streaming': True
        }

    async def send_tool_response(self, tool_call_id: str, result: Dict[str, Any]) -> None:
        """
        Handled by the orchestrator/ReAct loop usually.
        """
        pass

    async def interrupt(self) -> None:
        """Standard completions don't need interruption in the same way as audio streams."""
        pass

    async def cleanup(self) -> None:
        """No specific cleanup needed for LiteLLM stateless calls."""
        pass

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> GenerateResponse:
        """
        Unified generate method for ReAct orchestration.
        Delegates directly to the LiteLLMAdapter.
        """
        return await self.adapter.generate(
            messages=messages,
            tools=tools,
            temperature=temperature,
            on_thinking=on_thinking,
            on_token=on_token,
            **kwargs
        )

    def convert_tool_schemas(self, schemas: List[Dict[str, Any]]) -> List[Any]:
        return self.adapter.convert_tool_schemas(schemas)

    def build_tool_result_message(self, tool_call_id: str, name: str, result: Any) -> Dict[str, Any]:
        return self.adapter.build_tool_result_message(tool_call_id, name, result)

    def build_assistant_tool_call_message(self, tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self.adapter.build_assistant_tool_call_message(tool_calls)
