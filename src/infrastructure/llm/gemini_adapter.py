"""
Gemini LLM Adapter

Implements LLMPort interface for Google Gemini models.
"""

import logging
from typing import List, Dict, Any, Optional, AsyncIterator

from .llm_port import (
    LLMPort,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    LLMToolDefinition,
    LLMModelInfo,
    LLMRole
)

logger = logging.getLogger("GeminiAdapter")


class GeminiAdapter(LLMPort):
    """
    Adapter for Google Gemini API.

    Translates between LLMPort interface and Gemini API calls.
    """

    def __init__(
        self,
        model_name: str = "gemini-3.1-flash-lite-preview",
        api_key: Optional[str] = None,
        **config
    ):
        """
        Initialize Gemini adapter.

        Args:
            model_name: Gemini model identifier
            api_key: Google API key (if None, uses environment)
            **config: Additional Gemini configuration
        """
        self.model_name = model_name
        self.config = config

        # Initialize Gemini client
        import os
        import google.generativeai as genai
        api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        else:
            logger.warning("No GOOGLE_API_KEY found in environment or config.")

        # Create model instance
        self.model = genai.GenerativeModel(model_name)

        logger.info(f"GeminiAdapter initialized: {model_name}")

    def _convert_messages_to_gemini(
        self,
        messages: List[LLMMessage]
    ) -> List[Dict[str, Any]]:
        """Convert LLMMessage list to Gemini format"""
        gemini_messages = []

        for msg in messages:
            role = "user" if msg.role == LLMRole.USER else "model"
            gemini_msg = {
                "role": role,
                "parts": [{"text": msg.content}]
            }
            gemini_messages.append(gemini_msg)

        return gemini_messages

    def _convert_tools_to_gemini(
        self,
        tools: Optional[List[LLMToolDefinition]]
    ) -> Optional[List[Dict[str, Any]]]:
        """Convert LLMToolDefinition list to Gemini format"""
        if not tools:
            return None

        gemini_tools = []
        for tool in tools:
            gemini_tool = {
                "function_declarations": [{
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters
                }]
            }
            gemini_tools.append(gemini_tool)

        return gemini_tools

    def _extract_tool_calls(self, response) -> Optional[List[Dict[str, Any]]]:
        """Extract tool calls from Gemini response"""
        try:
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate.content, 'parts'):
                    tool_calls = []
                    for part in candidate.content.parts:
                        if hasattr(part, 'function_call'):
                            fc = part.function_call
                            tool_calls.append({
                                "id": fc.name,  # Gemini doesn't provide IDs
                                "name": fc.name,
                                "arguments": dict(fc.args)
                            })
                    return tool_calls if tool_calls else None
        except Exception as e:
            logger.debug(f"No tool calls in response: {e}")

        return None

    async def generate(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[LLMToolDefinition]] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """Generate complete response from Gemini"""
        try:
            # Convert to Gemini format
            gemini_messages = self._convert_messages_to_gemini(messages)
            gemini_tools = self._convert_tools_to_gemini(tools)

            # Build generation config
            gen_config = {
                "temperature": self.validate_temperature(temperature),
            }
            if max_tokens:
                gen_config["max_output_tokens"] = max_tokens

            # Generate response
            response = self.model.generate_content(
                gemini_messages,
                tools=gemini_tools,
                generation_config=gen_config,
                **kwargs
            )

            # Extract text content
            content = ""
            if hasattr(response, 'text'):
                content = response.text
            elif hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate.content, 'parts'):
                    content = " ".join(
                        part.text for part in candidate.content.parts
                        if hasattr(part, 'text')
                    )

            # Extract tool calls
            tool_calls = self._extract_tool_calls(response)

            # Extract usage stats
            usage = None
            if hasattr(response, 'usage_metadata'):
                usage = {
                    "prompt_tokens": response.usage_metadata.prompt_token_count,
                    "completion_tokens": response.usage_metadata.candidates_token_count,
                    "total_tokens": response.usage_metadata.total_token_count
                }

            return LLMResponse(
                content=content,
                role=LLMRole.ASSISTANT,
                tool_calls=tool_calls,
                finish_reason="stop",
                usage=usage
            )

        except Exception as e:
            logger.error(f"Gemini generation failed: {e}", exc_info=True)
            raise

    async def stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[LLMToolDefinition]] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream response from Gemini"""
        try:
            # Convert to Gemini format
            gemini_messages = self._convert_messages_to_gemini(messages)
            gemini_tools = self._convert_tools_to_gemini(tools)

            # Build generation config
            gen_config = {
                "temperature": self.validate_temperature(temperature),
            }
            if max_tokens:
                gen_config["max_output_tokens"] = max_tokens

            # Stream response
            response_stream = self.model.generate_content(
                gemini_messages,
                tools=gemini_tools,
                generation_config=gen_config,
                stream=True,
                **kwargs
            )

            for chunk in response_stream:
                # Extract text from chunk
                content = None
                if hasattr(chunk, 'text'):
                    content = chunk.text
                elif hasattr(chunk, 'candidates') and chunk.candidates:
                    candidate = chunk.candidates[0]
                    if hasattr(candidate.content, 'parts'):
                        texts = [
                            part.text for part in candidate.content.parts
                            if hasattr(part, 'text')
                        ]
                        if texts:
                            content = " ".join(texts)

                # Check if this is final chunk
                is_final = False
                finish_reason = None
                if hasattr(chunk, 'candidates') and chunk.candidates:
                    candidate = chunk.candidates[0]
                    if hasattr(candidate, 'finish_reason'):
                        finish_reason = str(candidate.finish_reason)
                        is_final = True

                # Extract tool calls (usually in final chunk)
                tool_calls = self._extract_tool_calls(chunk) if is_final else None

                yield LLMStreamChunk(
                    content=content,
                    role=LLMRole.ASSISTANT if content else None,
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                    is_final=is_final
                )

        except Exception as e:
            logger.error(f"Gemini streaming failed: {e}", exc_info=True)
            raise

    def count_tokens(self, text: str) -> int:
        """Count tokens using Gemini's count_tokens API"""
        try:
            response = self.model.count_tokens(text)
            return response.total_tokens
        except Exception as e:
            logger.warning(f"Token counting failed: {e}")
            # Rough estimate: 4 chars per token
            return len(text) // 4

    def get_model_info(self) -> LLMModelInfo:
        """Get Gemini model information"""
        # Model-specific capabilities (approximate)
        supports_vision = "vision" in self.model_name.lower() or "pro" in self.model_name.lower()

        return LLMModelInfo(
            provider="google",
            model_name=self.model_name,
            supports_streaming=True,
            supports_tools=True,
            supports_vision=supports_vision,
            max_tokens=8192,  # Gemini 2.0 default
            context_window=1048576 if "2.0" in self.model_name else 128000,
            metadata={"config": self.config}
        )

    async def close(self) -> None:
        """Clean up Gemini resources"""
        # Gemini SDK doesn't require explicit cleanup
        logger.debug("GeminiAdapter closed")
