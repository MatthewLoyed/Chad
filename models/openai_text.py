"""
OpenAI Text Model Implementation
Wraps the OpenAIAdapter to fulfill the Iteration 1 BaseModel interface.
Used for local models (LM Studio / Ollama) within the Brain's orchestration layer.
"""

from typing import Dict, Any, AsyncGenerator, Optional, List
import logging
import json

from models.base_model import BaseModel
from src.core.intelligence.adapters.openai_adapter import OpenAIAdapter
from src.core.intelligence.adapters.ai_adapter_base import GenerateResponse
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("OpenAITextModel")

class OpenAITextModel(BaseModel):
    """
    BaseModel implementation that uses the OpenAIAdapter.
    Enables local models like Qwen/Llama to be used via LM Studio or Ollama.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Extract configuration
        base_url = config.get('base_url') or ConfigManager.get("local_models.providers.lmstudio.base_url")
        api_key = config.get('api_key') or ConfigManager.get("local_models.providers.lmstudio.api_key")
        model_name = config.get('model') or ConfigManager.get_active_model()
        
        # Initialize the unified adapter
        self.adapter = OpenAIAdapter(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            system_instruction=config.get('system_instruction', ConfigManager.get_persona_instruction())
        )
        
        self.provider = config.get('provider', 'lmstudio')
        logger.info(f"[ LOCAL_MODEL ] Initialized {model_name} via {self.provider} ({base_url})")

    async def stream_audio(
        self, 
        audio_input: AsyncGenerator[bytes, None],
        tool_schemas: List[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        OpenAI Text model does not support native audio streaming.
        This would require a separate TTS/STT layer.
        """
        logger.warning("[ LOCAL_MODEL ] Native audio streaming not supported for this model.")
        yield {'type': 'error', 'message': 'Native audio streaming not supported for local text models.'}
        yield {'type': 'turn_complete'}

    async def execute_text(
        self, 
        prompt: str,
        tool_schemas: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Execute a text interaction.
        """
        # Convert tool schemas if provided
        openai_tools = None
        if tool_schemas:
            openai_tools = OpenAIAdapter.convert_tool_schemas(tool_schemas)
            
        # Use adapter to generate
        response: GenerateResponse = await self.adapter.generate(
            messages=[{"role": "user", "content": prompt}],
            tools=openai_tools,
            temperature=self.temperature
        )
        
        # Convert response to BaseModel format
        result = {
            'text': response.text,
            'tool_calls': response.tool_calls
        }
        return result

    def get_capabilities(self) -> Dict[str, bool]:
        return {
            'audio_input': False,
            'audio_output': False,
            'vision': False,
            'function_calling': True,
            'streaming': True
        }

    async def send_tool_response(self, tool_call_id: str, result: Dict[str, Any]) -> None:
        """
        Handled by the orchestrator/ReAct loop usually, but interface requires it.
        OpenAIAdapter manages its own conversation state if requested, 
        but usually the orchestrator passes full history.
        """
        pass

    async def interrupt(self) -> None:
        """Text models don't have a streaming output that needs interruption in the same way as audio."""
        pass

    async def cleanup(self) -> None:
        if self.adapter:
            await self.adapter.close()
